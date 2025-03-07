##############################################################################
## This file contains helper functions (metadata, filtering, external APIs) ##
##############################################################################
import os
import re
import json
import time
import asyncio
import aiohttp
import requests
import openai
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from urllib.parse import quote
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher

load_dotenv()

sp_oauth = SpotifyOAuth(
    client_id=os.environ.get("SPOTIPY_CLIENT_ID"),
    client_secret=os.environ.get("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.environ.get("SPOTIPY_REDIRECT_URI"),
    scope="user-top-read user-library-read"
)

ENABLE_SONG_EXPLANATION = False  # Set to True for extra explanations

CACHE_DIR = "cache"
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)
LIKED_SONGS_CACHE_FILENAME = os.path.join(CACHE_DIR, "liked_songs_cache.json")
LIKED_SONGS_CACHE_TTL = 43200  
TOP_ARTISTS_CACHE_FILENAME = os.path.join(CACHE_DIR, "top_artists_cache.json")
TOP_ARTISTS_CACHE_TTL = 21600  
TOP_TRACKS_CACHE_FILENAME = os.path.join(CACHE_DIR, "top_tracks_cache.json")
TOP_TRACKS_CACHE_TTL = 3600     

def load_cache(filename, ttl):
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
            if time.time() - data.get("timestamp", 0) < ttl:
                return data.get("items", None)
    return None

def save_cache(filename, items):
    data = {"timestamp": time.time(), "items": items}
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f)

def load_liked_songs_cache():
    return load_cache(LIKED_SONGS_CACHE_FILENAME, LIKED_SONGS_CACHE_TTL)

def save_liked_songs_cache(items):
    save_cache(LIKED_SONGS_CACHE_FILENAME, items)

async def fetch_liked_songs_batch(session, access_token, offset, limit=50, debug=False):
    url = "https://api.spotify.com/v1/me/tracks"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"limit": limit, "offset": offset}
    async with session.get(url, headers=headers, params=params) as resp:
        if resp.status != 200:
            if debug:
                print(f"[DEBUG] Error fetching batch at offset {offset}: HTTP {resp.status}")
            return None
        data = await resp.json()
        if debug:
            print(f"[DEBUG] Fetched {len(data.get('items', []))} songs at offset {offset}.")
        return data

async def async_get_all_liked_songs(access_token, debug=False, min_matches=None, target_artist=None, genres=None):
    cached = load_liked_songs_cache()
    if cached is not None:
        if debug:
            print(f"[DEBUG] Loaded {len(cached)} liked songs from cache.")
        return cached

    items = []
    limit = 50
    offset = 0
    async with aiohttp.ClientSession() as session:
        while True:
            data = await fetch_liked_songs_batch(session, access_token, offset, limit, debug)
            if not data:
                break
            batch = data.get("items", [])
            items.extend(batch)
            if debug:
                print(f"[DEBUG] Total songs so far: {len(items)}")
            if target_artist:
                count_artist = sum(
                    1 for track in batch if target_artist.lower() in track["track"]["artists"][0]["name"].lower()
                )
                if count_artist > 0 and len(items) >= 100:
                    if debug:
                        print(f"[DEBUG] Found enough songs for target artist '{target_artist}'.")
                    break
            elif genres and min_matches:
                with ThreadPoolExecutor() as executor:
                    futures = [
                        executor.submit(
                            song_matches_genre,
                            {"name": track["track"]["name"], "artist": track["track"]["artists"][0]["name"]},
                            genres
                        )
                        for track in batch
                    ]
                    matches = sum(f.result() for f in futures)
                if matches >= min_matches:
                    if debug:
                        print(f"[DEBUG] Reached minimum matching songs ({min_matches}) in current batch.")
                    break
            await asyncio.sleep(2)  
            if data.get("next") is None:
                break
            offset += limit
    save_liked_songs_cache(items)
    return items

def load_top_artists_cache():
    return load_cache(TOP_ARTISTS_CACHE_FILENAME, TOP_ARTISTS_CACHE_TTL)

def save_top_artists_cache(items):
    save_cache(TOP_ARTISTS_CACHE_FILENAME, items)

def load_top_tracks_cache():
    return load_cache(TOP_TRACKS_CACHE_FILENAME, TOP_TRACKS_CACHE_TTL)

def save_top_tracks_cache(items):
    save_cache(TOP_TRACKS_CACHE_FILENAME, items)

def label_song_with_artist_info(song, debug=False):
    """
    Enrich a song (with 'name' and 'artist') by fetching additional artist metadata.
    Adds a 'labeled_genres' field.
    """
    try:
        sp = spotipy.Spotify(auth_manager=sp_oauth)
        results = sp.search(q=f"artist:{song['artist']}", type="artist", limit=1)
        if results["artists"]["items"]:
            artist_info = results["artists"]["items"][0]
            song["labeled_genres"] = artist_info.get("genres", [])
            song["artist_info"] = artist_info
        else:
            song["labeled_genres"] = []
    except Exception as e:
        if debug:
            print(f"[DEBUG] Error labeling song {song['name']} by {song['artist']}: {e}")
        song["labeled_genres"] = []
    return song

def cache_labeled_liked_songs(access_token, debug=False):
    """
    Pre-fetch all liked songs, enrich each with artist metadata, and cache the labeled results.
    Now also cache album cover and Spotify URI data.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    liked_songs_raw = loop.run_until_complete(async_get_all_liked_songs(access_token, debug=debug))
    loop.close()
    liked_songs = [{
        "name": track["track"]["name"],
        "artist": track["track"]["artists"][0]["name"],
        "album_cover": track["track"]["album"]["images"][0]["url"] if track["track"]["album"]["images"] else "https://via.placeholder.com/200",
        "uri": track["track"]["uri"]
    } for track in liked_songs_raw]
    with ThreadPoolExecutor() as executor:
        labeled_songs = list(executor.map(lambda s: label_song_with_artist_info(s, debug=debug), liked_songs))
    filename = os.path.join(CACHE_DIR, "labeled_liked_songs_cache.json")
    data = {"timestamp": time.time(), "items": labeled_songs}
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f)
    if debug:
        print(f"[DEBUG] Cached {len(labeled_songs)} labeled liked songs.")
    return labeled_songs

def load_labeled_liked_songs_cache(ttl=43200, debug=False):
    """
    Load enriched liked songs data from cache if valid.
    """
    filename = os.path.join(CACHE_DIR, "labeled_liked_songs_cache.json")
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
            if time.time() - data.get("timestamp", 0) < ttl:
                if debug:
                    print(f"[DEBUG] Loaded {len(data.get('items', []))} labeled liked songs from cache.")
                return data.get("items", [])
    return None

def get_user_preferences(access_token=None, debug=False):
    if access_token:
        sp = spotipy.Spotify(auth=access_token)
    else:
        sp = spotipy.Spotify(auth_manager=sp_oauth)
    
    cached_top_artists = load_top_artists_cache()
    if cached_top_artists is not None:
        top_artists = cached_top_artists
        if debug:
            print(f"[DEBUG] Loaded {len(top_artists)} top artists from cache.")
    else:
        top_artists = sp.current_user_top_artists(limit=10)["items"]
        save_top_artists_cache(top_artists)
    
    artist_names = [artist["name"] for artist in top_artists]
    top_genres = list(set([genre for artist in top_artists for genre in artist["genres"]]))
    
    cached_top_tracks = load_top_tracks_cache()
    if cached_top_tracks is not None:
        top_tracks = cached_top_tracks
        if debug:
            print(f"[DEBUG] Loaded {len(top_tracks)} top tracks from cache.")
    else:
        top_tracks = sp.current_user_top_tracks(limit=10)["items"]
        save_top_tracks_cache(top_tracks)
    track_names = [{
        "name": track["name"],
        "artist": track["artists"][0]["name"],
        "album_cover": track["album"]["images"][0]["url"] if track["album"]["images"] else "https://via.placeholder.com/200",
        "uri": track["uri"]
    } for track in top_tracks]
    
    labeled_liked_songs = load_labeled_liked_songs_cache(ttl=43200, debug=debug)
    if labeled_liked_songs is None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        liked_songs_raw = loop.run_until_complete(async_get_all_liked_songs(access_token, debug=debug))
        loop.close()
        liked_songs = [{
            "name": track["track"]["name"],
            "artist": track["track"]["artists"][0]["name"],
            "album_cover": track["track"]["album"]["images"][0]["url"] if track["track"]["album"]["images"] else "https://via.placeholder.com/200",
            "uri": track["track"]["uri"]
        } for track in liked_songs_raw]
        with ThreadPoolExecutor() as executor:
            labeled_liked_songs = list(executor.map(lambda s: label_song_with_artist_info(s, debug=debug), liked_songs))
        save_cache(LIKED_SONGS_CACHE_FILENAME, liked_songs)
    liked_track_names = labeled_liked_songs
    
    return {
        "top_artists": artist_names,
        "top_genres": top_genres,
        "top_tracks": track_names,
        "liked_songs": liked_track_names
    }

def song_matches_genre(song, target_genres):
    if "labeled_genres" in song:
        for tg in target_genres:
            if any(tg.lower() in g.lower() for g in song["labeled_genres"]):
                return True
        return False
    artist_name = song["artist"]
    try:
        sp = spotipy.Spotify(auth_manager=sp_oauth)
        results = sp.search(q=f"artist:{artist_name}", type="artist", limit=1)
        if results["artists"]["items"]:
            artist_info = results["artists"]["items"][0]
            artist_genres = artist_info.get("genres", [])
            for tg in target_genres:
                if any(tg.lower() in ag.lower() for ag in artist_genres):
                    return True
        return False
    except Exception:
        return False

def get_song_metadata(track_name, artist_name):
    query = f"{track_name} - {artist_name}"
    query_encoded = quote(query)
    low_url = f"https://acousticbrainz.org/api/v1/{query_encoded}/low-level"
    response = requests.get(low_url)
    bpm = "Unknown"
    if response.status_code == 200:
        data = response.json()
        bpm = data.get("rhythm", {}).get("bpm", "Unknown")
    if bpm == "Unknown":
        high_url = f"https://acousticbrainz.org/api/v1/{query_encoded}/high-level"
        response = requests.get(high_url)
        if response.status_code == 200:
            data = response.json()
            bpm = data.get("rhythm", {}).get("bpm", "Unknown")
    return {"bpm": bpm, "mood": "Unknown"}

def get_reference_track_details(reference_track, debug=False):
    if " by " in reference_track.lower():
        parts = re.split(r"\s+by\s+", reference_track, flags=re.IGNORECASE)
        track_name = parts[0].strip()
        artist_name = parts[1].strip() if len(parts) > 1 else None
    else:
        track_name = reference_track.strip()
        artist_name = None
    api_key = os.environ.get("LASTFM_API_KEY")
    url = "http://ws.audioscrobbler.com/2.0/"
    params = {
         "method": "track.search",
         "track": track_name,
         "api_key": api_key,
         "format": "json",
         "limit": 5
    }
    if artist_name:
        params["artist"] = artist_name
    response = requests.get(url, params=params)
    if response.status_code == 200:
         data = response.json()
         tracks = data.get("results", {}).get("trackmatches", {}).get("track", [])
         if isinstance(tracks, list) and len(tracks) > 0:
              return {"title": tracks[0].get("name"), "artist": tracks[0].get("artist")}
         elif isinstance(tracks, dict):
              return {"title": tracks.get("name"), "artist": tracks.get("artist")}
    if debug:
         print(f"[DEBUG] get_reference_track_details: No track found for '{reference_track}'")
    return None

def get_similar_tracks_lastfm(reference_track, reference_artist, limit=5, debug=False):
    api_key = os.environ.get("LASTFM_API_KEY")
    url = "http://ws.audioscrobbler.com/2.0/"
    params = {
         "method": "track.getSimilar",
         "track": reference_track,
         "artist": reference_artist,
         "api_key": api_key,
         "format": "json",
         "limit": limit
    }
    response = requests.get(url, params=params)
    if response.status_code == 200:
         raw = response.text
         if debug:
             print(f"[DEBUG] Last.fm raw response: {raw}")
         data = response.json()
         similar_tracks = data.get("similartracks", {}).get("track", [])
         if not similar_tracks and debug:
             print("[DEBUG] Last.fm returned no similar tracks.")
         recommendations = []
         for track in similar_tracks:
             recommendations.append({
                  "title": track.get("name"),
                  "artist": track.get("artist", {}).get("name"),
                  "bpm": "Unknown",
                  "release_year": "Unknown",
                  "liked": False,
                  "mood": "Unknown"
             })
         return recommendations
    else:
         if debug:
             print(f"[DEBUG] Last.fm API error: Status code {response.status_code}")
    return []

def interpret_user_query(user_query, debug=False):
    reasoning = [] if debug else None
    if debug:
        reasoning.append(f"I received the following query: '{user_query}'.")
    duration_match = re.search(r"(\d+(\.\d+)?)\s*(hour|hr|min|minutes)", user_query, re.IGNORECASE)
    extracted_duration = None
    if duration_match:
        duration_value = float(duration_match.group(1))
        if "hour" in duration_match.group(3).lower():
            extracted_duration = int(duration_value * 60)
        else:
            extracted_duration = int(duration_value)
        if debug:
            reasoning.append(f"Extracted duration: {extracted_duration} minutes.")
    else:
        if debug:
            reasoning.append("No explicit duration found; defaulting to 60 minutes.")
        extracted_duration = 60
    genres, mood_constraints = [], []
    if any(term in user_query.lower() for term in ["movie", "soundtrack", "cinematic"]):
        genres = ["orchestral", "cinematic", "electronic", "synthwave", "ambient"]
        mood_constraints = ["epic", "dramatic", "adventurous"]
        if debug:
            reasoning.append("Cinematic theme detected; set genres and mood constraints accordingly.")
    elif any(term in user_query.lower() for term in ["relax", "stress"]):
        genres = ["lofi", "chill", "ambient", "soft rock", "indie"]
        mood_constraints = ["calm", "peaceful", "soothing"]
        if debug:
            reasoning.append("Relaxation theme detected; set calming genres and mood constraints.")
    else:
        known_genres = ["bollywood", "hollywood", "disney", "pop", "rock", "hip hop", "rap", "jazz", "classical", "electronic", "edm", "country", "indie", "metal", "reggae", "r&b"]
        detected_genre = None
        for genre in known_genres:
            if genre in user_query.lower():
                detected_genre = genre
                if debug:
                    reasoning.append(f"Detected specific genre: {genre}.")
                break
        if detected_genre:
            genres = [detected_genre]
    if debug and not genres:
        reasoning.append("No specific genre detected; defaulting to 'any'.")
    concern_bpm = bool(re.search(r"\bBPM\b", user_query, re.IGNORECASE))
    if debug:
        reasoning.append("BPM validation " + ("enabled." if concern_bpm else "skipped."))
    use_only_user_songs = any(
        term in user_query.lower() for term in ["only my liked songs", "using my liked songs", "using my favorites", "using only my liked songs", "using only my favorites"]
    )
    if debug:
        reasoning.append("Personal songs constraint " + ("enabled." if use_only_user_songs else "not specified."))
    gradual_bpm = bool(re.search(r"(increase|progress)", user_query, re.IGNORECASE))
    if debug and gradual_bpm:
        reasoning.append("Gradual BPM progression requested.")
    instrument_list = ["guitar", "piano", "violin", "drums", "saxophone", "flute", "bass", "cello", "trumpet", "harp", "ukulele", "mandolin"]
    detected_instrument = None
    for inst in instrument_list:
        if inst in user_query.lower():
            detected_instrument = inst
            if debug:
                reasoning.append(f"Detected instrument: {inst}.")
            break
    target_artist = None
    m = re.search(r"by ([\w\s]+)$", user_query, re.IGNORECASE)
    if m:
        target_artist = m.group(1).strip()
        if debug:
            reasoning.append(f"Detected target artist: {target_artist}.")
    exclude_artist_flag = "not his music" in user_query.lower() or "but are not his music" in user_query.lower()
    extracted_data = {
        "explicit_song_count": None,
        "duration_minutes": extracted_duration,
        "bpm_range": [60, 130],
        "genres": genres if genres else ["any"],
        "release_year_range": [2019, 2024],
        "mood_constraints": mood_constraints if mood_constraints else [],
        "use_only_user_songs": use_only_user_songs,
        "reference_track": None,
        "concern_bpm": concern_bpm,
        "gradual_bpm": gradual_bpm,
        "instrument": detected_instrument,
        "exclude_artist": None,
        "target_artist": target_artist
    }
    prompt = f"""
    Please extract structured playlist constraints from the following query:
    "{user_query}"
    
    The JSON response should include: 
    "explicit_song_count", "duration_minutes", "bpm_range", "genres", "release_year_range", "mood_constraints", "use_only_user_songs", "reference_track", "instrument".
    If a song count is mentioned, set "explicit_song_count" to that number; otherwise, null.
    If a specific song is mentioned (e.g., "like Halloween by Novo Amor"), set "reference_track" accordingly; otherwise, null.
    If an instrument is mentioned (e.g., "guitar pieces"), set "instrument" accordingly.
    """
    if debug:
        reasoning.append("Constructed prompt for OpenAI API:")
        reasoning.append(prompt)
    try:
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Extract playlist constraints as JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.5
        )
        raw_content = response.choices[0].message.content.strip()
        if debug:
            reasoning.append(f"OpenAI API returned: {raw_content}")
        raw_content = raw_content.strip("```json").strip("```").strip()
        extracted_json = json.loads(raw_content)
    except json.JSONDecodeError as e:
        if debug:
            reasoning.append(f"JSON decode error: {e}. Using default constraints.")
        extracted_json = {}
    except Exception as e:
        if debug:
            reasoning.append(f"Error calling OpenAI API: {e}. Using default constraints.")
        extracted_json = {}
    if extracted_json.get("duration_minutes") is None:
        extracted_json["duration_minutes"] = extracted_duration
    if extracted_json.get("bpm_range") is None:
        extracted_json["bpm_range"] = [60, 130]
    if extracted_json.get("genres") is None:
        extracted_json["genres"] = genres if genres else ["any"]
    if extracted_json.get("release_year_range") is None:
        extracted_json["release_year_range"] = [2019, 2024]
    if extracted_json.get("mood_constraints") is None:
        extracted_json["mood_constraints"] = mood_constraints if mood_constraints else []
    if extracted_json.get("use_only_user_songs") is None:
        extracted_json["use_only_user_songs"] = use_only_user_songs
    if extracted_json.get("explicit_song_count") is None:
        extracted_json["explicit_song_count"] = None
    if extracted_json.get("reference_track") is None:
        extracted_json["reference_track"] = None
    if extracted_json.get("instrument") is None:
        extracted_json["instrument"] = detected_instrument
    extracted_json["concern_bpm"] = concern_bpm
    extracted_json["gradual_bpm"] = gradual_bpm
    if not extracted_json.get("reference_track"):
        m = re.search(r'sound like ([\w\s]+?)\s+but', user_query.lower())
        if m:
            extracted_json["reference_track"] = m.group(1).strip()
            if debug:
                reasoning.append(f"Extracted reference track via regex: {extracted_json['reference_track']}")
    if exclude_artist_flag and extracted_json.get("reference_track"):
        ref_details = get_reference_track_details(extracted_json["reference_track"], debug)
        if ref_details and ref_details.get("artist"):
            extracted_json["exclude_artist"] = ref_details.get("artist").strip().lower()
        else:
            extracted_json["exclude_artist"] = extracted_json["reference_track"].strip().lower()
    else:
        extracted_json["exclude_artist"] = None
    if debug:
        reasoning.append(f"Final extracted constraints: {extracted_json}")
        return extracted_json, reasoning
    else:
        return extracted_json

def matches_mood(song_mood, mood_constraints):
    if not mood_constraints or not song_mood:
        return True
    return any(mood.lower() in song_mood.lower() for mood in mood_constraints)

def validate_playlist(playlist, constraints, debug=False):
    validation_log = [] if debug else None
    header = ("Summary of validation:\n"
              f"- Query: '{constraints.get('user_query', 'N/A')}'.\n"
              f"- Duration: {constraints.get('duration_minutes')} min, BPM range: {constraints.get('bpm_range')}, "
              f"Genres: {constraints.get('genres')}, Mood: {constraints.get('mood_constraints')}.\n"
              f"- Instrument: {constraints.get('instrument') if constraints.get('instrument') else 'none'}.\n"
              f"- Personal songs only: {constraints.get('use_only_user_songs')}.\n"
              f"- BPM validation is {'enabled' if constraints.get('concern_bpm') else 'disabled'} and gradual BPM progression is {'requested' if constraints.get('gradual_bpm') else 'not requested'}.\n")
    if constraints.get("exclude_artist"):
        header += f"- Excluding artist: {constraints.get('exclude_artist')}.\n"
    validation_log.append(header)
    for i, song in enumerate(playlist):
        msg = f"Song '{song['title']}' by {song['artist']}: "
        if constraints.get("gradual_bpm"):
            num_songs = len(playlist)
            expected_bpm = constraints["bpm_range"][0]
            if num_songs > 1:
                expected_bpm += i * (constraints["bpm_range"][1] - constraints["bpm_range"][0]) / (num_songs - 1)
            msg += f"Expected BPM around {round(expected_bpm)}. "
            if "bpm" in song and song["bpm"] != "Unknown":
                bpm = song["bpm"]
                if constraints["bpm_range"][0] <= bpm <= constraints["bpm_range"][1]:
                    msg += f"Actual BPM is {bpm} (within range). "
                else:
                    msg += f"Actual BPM is {bpm} (outside range). "
            else:
                song["bpm"] = round(expected_bpm)
                msg += f"Assigned expected BPM of {round(expected_bpm)}. "
        elif constraints.get("concern_bpm"):
            if "bpm" in song and song["bpm"] != "Unknown":
                bpm = song["bpm"]
                if constraints["bpm_range"][0] <= bpm <= constraints["bpm_range"][1]:
                    msg += f"BPM {bpm} is within range. "
                else:
                    msg += f"BPM {bpm} is outside the range {constraints['bpm_range']}. "
            else:
                fallback_bpm = int((constraints["bpm_range"][0] + constraints["bpm_range"][1]) / 2)
                song["bpm"] = fallback_bpm
                msg += f"Assigned fallback BPM of {fallback_bpm}. "
        else:
            msg += "No BPM validation performed. "
        if "source" in song and "reason" in song:
            msg += f"Source: {song['source']} because {song['reason']}."
        else:
            msg += "No additional source info."
        validation_log.append(msg)
    return validation_log

def similar(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def find_cached_match(song, cached_songs, threshold=0.7):
    song_title = song.get("title") or song.get("name", "")
    for csong in cached_songs:
        cached_title = csong.get("name", "")
        title_ratio = similar(song_title, cached_title)
        artist_ratio = similar(song["artist"], csong["artist"])
        if title_ratio > threshold and artist_ratio > threshold:
            return csong
    return None

def generate_constrained_playlist(user_query, access_token=None, debug=False):
    """
    Generates a playlist based on the user query and enriches each track with its album cover and track URI.
    Now avoids using Spotify search/recommendation endpoints by leveraging cached real song data.
    """
    if debug:
        constraints, reasoning = interpret_user_query(user_query, debug=debug)
    else:
        constraints = interpret_user_query(user_query, debug=debug)
        reasoning = None
    constraints["user_query"] = user_query
    if constraints.get("explicit_song_count") is not None:
        num_songs = int(constraints["explicit_song_count"])
        if debug:
            reasoning.append(f"Using explicit song count: {num_songs}.")
    else:
        duration = constraints["duration_minutes"]
        avg_song_length = 4
        num_songs = max(5, round(duration / avg_song_length))
        if debug:
            reasoning.append(f"Calculated playlist should contain about {num_songs} songs based on duration.")
    bpm_range = constraints["bpm_range"]
    genres = constraints["genres"]
    release_year_range = constraints["release_year_range"]
    mood_constraints = constraints["mood_constraints"]
    use_only_user_songs = constraints.get("use_only_user_songs", False)
    reference_track = constraints.get("reference_track", None)
    if isinstance(bpm_range, list) and len(bpm_range) == 2:
        bpm_start, bpm_end = bpm_range
    else:
        bpm_start, bpm_end = 60, 130
    filtered_songs = []
    if use_only_user_songs:
        user_data = get_user_preferences(access_token=access_token, debug=debug)
        user_songs = user_data["liked_songs"] + user_data["top_tracks"]
        with ThreadPoolExecutor() as executor:
            futures = {}
            for song in user_songs:
                if constraints.get("target_artist"):
                    if constraints["target_artist"].lower() not in song["artist"].lower():
                        continue
                futures[executor.submit(song_matches_genre, song, genres)] = song
            for future in futures:
                try:
                    if future.result():
                        song = futures[future]
                        song["source"] = "personal"
                        song["reason"] = f"Matches genre constraint {genres}."
                        filtered_songs.append(song)
                        if debug:
                            reasoning.append(f"Song '{song['name']}' by {song['artist']} matches genre {genres}.")
                except Exception:
                    continue
        if debug:
            reasoning.append(f"After filtering, {len(filtered_songs)} personal songs remain.")
    else:
        if reference_track:
            ref_details = get_reference_track_details(reference_track, debug)
            if ref_details is not None:
                reference_track_name = ref_details.get("title")
                reference_artist = ref_details.get("artist")
                if debug:
                    reasoning.append(f"Reference track details: '{reference_track_name}' by '{reference_artist}'.")
            else:
                reference_track_name = reference_track
                reference_artist = "Unknown"
                if debug:
                    reasoning.append("Using provided reference track text; detailed info not found.")
            external_recs = get_similar_tracks_lastfm(reference_track_name, reference_artist, limit=num_songs, debug=debug)
            if external_recs:
                for rec in external_recs:
                    rec["source"] = "Last.fm"
                    rec["reason"] = (f"Recommended by Last.fm based on reference track '{reference_track_name}' by '{reference_artist}'.")
                filtered_songs += external_recs
                if debug:
                    reasoning.append(f"Received {len(external_recs)} recommendations from Last.fm.")
            else:
                if debug:
                    reasoning.append("No recommendations from Last.fm; falling back on AI generation.")
    if constraints.get("exclude_artist"):
        exclude = constraints["exclude_artist"].strip().lower()
        before = len(filtered_songs)
        filtered_songs = [song for song in filtered_songs if song["artist"].strip().lower() != exclude]
        if debug:
            reasoning.append(f"Excluded {before - len(filtered_songs)} song(s) by '{exclude}'.")
    if len(filtered_songs) < num_songs:
        needed = num_songs - len(filtered_songs)
        reference_line = ""
        if reference_track:
            reference_line = (f"Reference track: {reference_track}. Generate similar songs.")
        instrument_line = ""
        if constraints.get("instrument"):
            instrument_line = f" Include songs with prominent {constraints.get('instrument')}."
        prompt = f"""
        Generate a playlist with the following constraints:
        - Genre: {genres}
        - BPM range: {bpm_start} to {bpm_end}
        - Release years: {release_year_range[0]} to {release_year_range[1]}
        - Mood constraints: {mood_constraints}
        - Number of songs: {needed}
        {reference_line}
        {instrument_line}
        
        Respond in JSON format as a list of objects with keys "title", "artist", "bpm", and "release_year".
        """
        if debug:
            reasoning.append("Prompting AI to generate additional songs with:")
            reasoning.append(prompt)
        try:
            response = openai.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a music expert AI that generates playlists."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7
            )
            raw_content = response.choices[0].message.content
            json_part = re.search(r"\[\s*{.*}\s*\]", raw_content, re.DOTALL)
            if json_part:
                ai_songs = json.loads(json_part.group(0))
                for song in ai_songs:
                    song["liked"] = False
                    song["source"] = "AI"
                    song["reason"] = "Generated by AI to meet remaining song count."
                filtered_songs += ai_songs
                if debug:
                    reasoning.append(f"AI generated {len(ai_songs)} songs, added to playlist.")
            else:
                if debug:
                    reasoning.append("AI response did not return valid JSON; no songs added.")
        except Exception as e:
            if debug:
                reasoning.append(f"Error during AI generation: {str(e)}")
    for song in filtered_songs:
        if song.get("bpm", "Unknown") == "Unknown":
            fallback_bpm = int((bpm_start + bpm_end) / 2)
            song["bpm"] = fallback_bpm
    if constraints.get("gradual_bpm"):
        filtered_songs = sorted(filtered_songs, key=lambda s: s.get("bpm", int((bpm_start+bpm_end)/2)))
        if debug:
            reasoning.append("Sorted songs by BPM for gradual progression.")
    
    user_data = get_user_preferences(access_token=access_token, debug=debug)
    cached_songs = user_data["liked_songs"] + user_data["top_tracks"]
    
    enriched_songs = []
    for song in filtered_songs[:num_songs]:
        if not song.get("album_cover") or not song.get("uri"):
            match = find_cached_match(song, cached_songs)
            if match:
                song["album_cover"] = match.get("album_cover", "https://via.placeholder.com/200")
                song["uri"] = match.get("uri")
        if not song.get("album_cover"):
            song["album_cover"] = "https://via.placeholder.com/200"
        enriched_songs.append(song)
    
    playlist = [
        {"title": song.get("name", song.get("title")),
         "artist": song["artist"],
         "liked": song.get("liked", False),
         "mood": song.get("mood", "Unknown"),
         "source": song.get("source", "unknown"),
         "reason": song.get("reason", "No reason provided"),
         "bpm": song.get("bpm", "Unknown"),
         "album_cover": song.get("album_cover"),
         "uri": song.get("uri")}
        for song in enriched_songs
    ]
    if debug:
        validation_log = validate_playlist(playlist, constraints, debug=debug)
        reasoning.append("Final validation summary:")
        reasoning.extend(validation_log)
        return {"playlist": playlist, "reasoning": reasoning}
    else:
        return {"playlist": playlist}
