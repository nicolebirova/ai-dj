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

load_dotenv()

sp_oauth = SpotifyOAuth(
    client_id=os.environ.get("SPOTIPY_CLIENT_ID"),
    client_secret=os.environ.get("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.environ.get("SPOTIPY_REDIRECT_URI"),
    scope="user-top-read user-library-read"
)

ENABLE_SONG_EXPLANATION = False  # Set to True to get extra explanation per song.
CACHE_FILENAME = "liked_songs_cache.json"
CACHE_TTL = 43200  # seconds (12 hours)

# caching
def load_liked_songs_cache():
    if os.path.exists(CACHE_FILENAME):
        with open(CACHE_FILENAME, "r", encoding="utf-8") as f:
            data = json.load(f)
            if time.time() - data.get("timestamp", 0) < CACHE_TTL:
                return data.get("items", [])
    return None

def save_liked_songs_cache(items):
    data = {"timestamp": time.time(), "items": items}
    with open(CACHE_FILENAME, "w", encoding="utf-8") as f:
        json.dump(data, f)

# retrieving
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

async def async_get_all_liked_songs(access_token, debug=False, min_matches=None, genres=None):
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
            if genres and min_matches:
                with ThreadPoolExecutor() as executor:
                    futures = [executor.submit(song_matches_genre, 
                                               {"name": track["track"]["name"], "artist": track["track"]["artists"][0]["name"]}, 
                                               genres) for track in batch]
                    matches = sum(f.result() for f in futures)
                if matches >= min_matches:
                    if debug:
                        print(f"[DEBUG] Reached minimum matching songs ({min_matches}) in current batch.")
                    break
            if data.get("next") is None:
                break
            offset += limit
    save_liked_songs_cache(items)
    return items

def explain_song_selection(song, constraints):
    prompt = (f"Explain briefly why the song '{song.get('title')}' by '{song.get('artist')}' " 
              f"meets the following constraints: a BPM range of {constraints.get('bpm_range')}, "
              f"genres {constraints.get('genres')}, instrument {constraints.get('instrument')}, "
              f"and release years {constraints.get('release_year_range')}.")
    try:
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a music analyst who explains decisions in plain language."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.5,
            max_tokens=60
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return "No additional explanation is available at this time."

def get_user_preferences(access_token=None, debug=False):
    if access_token:
        sp = spotipy.Spotify(auth=access_token)
    else:
        sp = spotipy.Spotify(auth_manager=sp_oauth)
    top_artists = sp.current_user_top_artists(limit=10)["items"]
    artist_names = [artist["name"] for artist in top_artists]
    top_genres = list(set([genre for artist in top_artists for genre in artist["genres"]]))
    top_tracks = sp.current_user_top_tracks(limit=10)["items"]
    track_names = [{"name": track["name"], "artist": track["artists"][0]["name"]} for track in top_tracks]
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    liked_songs_raw = loop.run_until_complete(async_get_all_liked_songs(access_token, debug=debug))
    loop.close()
    liked_track_names = [{"name": track["track"]["name"], "artist": track["track"]["artists"][0]["name"]} for track in liked_songs_raw]
    
    return {
        "top_artists": artist_names,
        "top_genres": top_genres,
        "top_tracks": track_names,
        "liked_songs": liked_track_names
    }

def song_matches_genre(song, target_genres):
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
    except Exception as e:
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
            reasoning.append(f"I extracted a duration of {extracted_duration} minutes from the query.")
    else:
        if debug:
            reasoning.append("I did not find an explicit duration in the query.")
    if extracted_duration is None:
        extracted_duration = 60
        if debug:
            reasoning.append("So, I defaulted the duration to 60 minutes.")

    genres, mood_constraints = [], []
    if any(term in user_query.lower() for term in ["movie", "soundtrack", "cinematic"]):
        genres = ["orchestral", "cinematic", "electronic", "synthwave", "ambient"]
        mood_constraints = ["epic", "dramatic", "adventurous"]
        if debug:
            reasoning.append("The query appears to be about a cinematic theme; set genres and mood constraints accordingly.")
    elif any(term in user_query.lower() for term in ["relax", "stress"]):
        genres = ["lofi", "chill", "ambient", "soft rock", "indie"]
        mood_constraints = ["calm", "peaceful", "soothing"]
        if debug:
            reasoning.append("The query seems to aim for relaxation; set calming genres and mood constraints.")
    else:
        known_genres = ["bollywood", "hollywood", "disney", "pop", "rock", "hip hop", "rap", "jazz", "classical", "electronic", "edm", "country", "indie", "metal", "reggae", "r&b"]
        detected_genre = None
        for genre in known_genres:
            if genre in user_query.lower():
                detected_genre = genre
                if debug:
                    reasoning.append(f"Detected a specific genre: {genre}.")
                break
        if detected_genre:
            genres = [detected_genre]
    if debug and not genres:
        reasoning.append("No specific genre detected; defaulting to 'any'.")

    concern_bpm = bool(re.search(r"\bBPM\b", user_query, re.IGNORECASE))
    if debug:
        if concern_bpm:
            reasoning.append("Query mentions BPM; will perform BPM validation.")
        else:
            reasoning.append("Query does not mention BPM; skipping BPM validation.")

    use_only_user_songs = any(
        term in user_query.lower() for term in ["only my liked songs", "using my liked songs", "using my favorites", "using only my liked songs", "using only my favorites"]
    )
    if debug:
        if use_only_user_songs:
            reasoning.append("User requested only personal songs.")
        else:
            reasoning.append("No personal-only songs constraint specified.")

    gradual_bpm = False
    if re.search(r"increase", user_query, re.IGNORECASE) or re.search(r"progress", user_query, re.IGNORECASE):
        gradual_bpm = True
        if debug:
            reasoning.append("Query indicates gradual BPM progression.")

    instrument_list = ["guitar", "piano", "violin", "drums", "saxophone", "flute", "bass", "cello", "trumpet", "harp", "ukulele", "mandolin"]
    detected_instrument = None
    for inst in instrument_list:
        if inst in user_query.lower():
            detected_instrument = inst
            if debug:
                reasoning.append(f"Detected instrument: {inst}.")
            break

    exclude_artist_flag = False
    if "not his music" in user_query.lower() or "but are not his music" in user_query.lower():
        exclude_artist_flag = True

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
        "exclude_artist": None
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
        reasoning.append("Constructed prompt for OpenAI API to extract constraints:")
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

def generate_constrained_playlist(user_query, access_token=None, debug=False):
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
        # Combine liked songs and top tracks.
        user_songs = user_data["liked_songs"] + user_data["top_tracks"]
        # Use parallel processing to filter songs by genre.
        with ThreadPoolExecutor() as executor:
            futures = {executor.submit(song_matches_genre, song, genres): song for song in user_songs}
            for future in futures:
                try:
                    if future.result():
                        song = futures[future]
                        song["source"] = "personal"
                        song["reason"] = f"Matches genre constraint {genres}."
                        filtered_songs.append(song)
                        if debug:
                            reasoning.append(f"Song '{song['name']}' by {song['artist']} matches genre {genres}.")
                except Exception as e:
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

    needed = num_songs - len(filtered_songs)
    if needed > 0:
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
    
    # For any song missing BPM, assign fallback once (without repetitive messages).
    for song in filtered_songs:
        if song.get("bpm", "Unknown") == "Unknown":
            fallback_bpm = int((bpm_start + bpm_end) / 2)
            song["bpm"] = fallback_bpm
    
    if constraints.get("gradual_bpm"):
        filtered_songs = sorted(filtered_songs, key=lambda s: s.get("bpm", int((bpm_start+bpm_end)/2)))
        if debug:
            reasoning.append("Sorted songs by BPM for gradual progression.")

    user_data = get_user_preferences(access_token=access_token, debug=debug)
    personal_tracks = {
        (song["name"].strip().lower(), song["artist"].strip().lower())
        for song in user_data["liked_songs"] + user_data["top_tracks"]
    }
    for song in filtered_songs:
        key = (song.get("name", "").strip().lower(), song.get("artist", "").strip().lower())
        if key in personal_tracks:
            song["liked"] = True

    playlist = [
        {"title": song.get("name", song.get("title")),
         "artist": song["artist"],
         "liked": song.get("liked", False),
         "mood": song.get("mood", "Unknown"),
         "source": song.get("source", "unknown"),
         "reason": song.get("reason", "No reason provided"),
         "bpm": song.get("bpm", "Unknown")}
        for song in filtered_songs[:num_songs]
    ]
    if debug:
        validation_log = validate_playlist(playlist, constraints, debug=debug)
        reasoning.append("Final validation summary:")
        reasoning.extend(validation_log)
        return {"playlist": playlist, "reasoning": reasoning}
    else:
        return {"playlist": playlist}
