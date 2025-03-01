##############################################################################
## This file contains helper functions (metadata, filtering, external APIs) ##
##############################################################################
import spotipy
import requests
import openai
import os
import json
import re
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

load_dotenv()

sp_oauth = SpotifyOAuth(
    client_id=os.environ.get("SPOTIPY_CLIENT_ID"),
    client_secret=os.environ.get("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.environ.get("SPOTIPY_REDIRECT_URI"),
    scope="user-top-read user-library-read"
)

ENABLE_SONG_EXPLANATION = True

def explain_song_selection(song, constraints):
    """
    Optionally ask OpenAI to explain why a given song meets the constraints.
    """
    prompt = f"Explain briefly why the song '{song.get('title')}' by '{song.get('artist')}' meets these constraints: BPM range {constraints.get('bpm_range')}, genres {constraints.get('genres')}, and release years {constraints.get('release_year_range')}."
    try:
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": "You are a helpful music analyst."},
                      {"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=60
        )
        explanation = response.choices[0].message.content.strip()
        return explanation
    except Exception as e:
        return "No explanation available."

def get_user_preferences(access_token=None):
    if access_token:
        sp = spotipy.Spotify(auth=access_token)
    else:
        sp = spotipy.Spotify(auth_manager=sp_oauth)
    top_artists = sp.current_user_top_artists(limit=10)["items"]
    artist_names = [artist["name"] for artist in top_artists]
    top_genres = list(set([genre for artist in top_artists for genre in artist["genres"]]))
    top_tracks = sp.current_user_top_tracks(limit=10)["items"]
    track_names = [{"name": track["name"], "artist": track["artists"][0]["name"]} for track in top_tracks]
    liked_songs = sp.current_user_saved_tracks(limit=10)["items"]
    liked_track_names = [{"name": track["track"]["name"], "artist": track["track"]["artists"][0]["name"]} for track in liked_songs]
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
    query_url = f"https://acousticbrainz.org/api/v1/{track_name} - {artist_name}/low-level"
    response = requests.get(query_url)
    if response.status_code == 200:
        data = response.json()
        bpm = data.get("rhythm", {}).get("bpm", "Unknown")
        mood = data.get("highlevel", {}).get("mood_acoustic", {}).get("value", "Unknown")
        return {"bpm": bpm, "mood": mood}
    return {"bpm": "Unknown", "mood": "Unknown"}

def get_reference_track_details(reference_track):
    """
    Look up the reference track on Last.fm.
    If the input contains a 'by' clause (e.g. "Neon Moon by Brooks & Dunn"),
    split it into track and artist, and use both in the search.
    Returns a dict with keys "title" and "artist" or None if not found.
    """
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
         "limit": 1
    }
    if artist_name:
        params["artist"] = artist_name
    response = requests.get(url, params=params)
    if response.status_code == 200:
         data = response.json()
         tracks = data.get("results", {}).get("trackmatches", {}).get("track", [])
         if isinstance(tracks, list) and len(tracks) > 0:
              best = tracks[0]
              return {
                  "title": best.get("name"),
                  "artist": best.get("artist")
              }
         elif isinstance(tracks, dict):
              return {
                  "title": tracks.get("name"),
                  "artist": tracks.get("artist")
              }
    return None

def get_similar_tracks_lastfm(reference_track, reference_artist, limit=5, debug=False):
    """
    Uses the Last.fm API (track.getSimilar) to retrieve similar tracks based on the
    reference track and artist. If debug is True, prints the raw response.
    """
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
        reasoning.append(f"Received user query: '{user_query}'")

    duration_match = re.search(r"(\d+(\.\d+)?)\s*(hour|hr|min|minutes)", user_query, re.IGNORECASE)
    extracted_duration = None
    if duration_match:
        duration_value = float(duration_match.group(1))
        if "hour" in duration_match.group(3).lower():
            extracted_duration = int(duration_value * 60)
        else:
            extracted_duration = int(duration_value)
        if debug:
            reasoning.append(f"Extracted duration: {extracted_duration} minutes from query.")
    else:
        if debug:
            reasoning.append("No explicit duration found in query.")
    if extracted_duration is None:
        extracted_duration = 60
        if debug:
            reasoning.append("Defaulting duration to 60 minutes as generic fallback.")

    genres, mood_constraints = [], []
    if "movie" in user_query.lower() or "soundtrack" in user_query.lower() or "cinematic" in user_query.lower():
        genres = ["orchestral", "cinematic", "electronic", "synthwave", "ambient"]
        mood_constraints = ["epic", "dramatic", "adventurous"]
        if debug:
            reasoning.append("Query indicates cinematic theme; using cinematic genres and mood constraints.")
    elif "relax" in user_query.lower() or "stress" in user_query.lower():
        genres = ["lofi", "chill", "ambient", "soft rock", "indie"]
        mood_constraints = ["calm", "peaceful", "soothing"]
        if debug:
            reasoning.append("Query indicates relaxation; using calming genres and mood constraints.")
    else:
        known_genres = ["bollywood", "hollywood", "disney", "pop", "rock", "hip hop", "rap", "jazz", "classical", "electronic", "edm", "country", "indie", "metal", "reggae", "r&b"]
        detected_genre = None
        for genre in known_genres:
            if genre in user_query.lower():
                detected_genre = genre
                if debug:
                    reasoning.append(f"Detected specific genre: {genre} in user query.")
                break
        if detected_genre:
            genres = [detected_genre]
    if debug and not genres:
        reasoning.append("No specific genre detected; defaulting to 'any'.")

    use_only_user_songs = any(
        term in user_query.lower() for term in ["only my liked songs", "using my liked songs", "using my favorites", "using only my liked songs", "using only my favorites"]
    )
    if debug:
        if use_only_user_songs:
            reasoning.append("User requested to use only their personal songs.")
        else:
            reasoning.append("User did not request to exclusively use personal songs.")

    prompt = f"""
    Extract structured playlist constraints from the following user request:
    "{user_query}"
    
    Response must be valid JSON with the following keys:
    {{
      "explicit_song_count": <number or null>, 
      "duration_minutes": {extracted_duration},
      "bpm_range": [60, 130],
      "genres": {genres if genres else '["any"]'},
      "release_year_range": [2019, 2024],
      "mood_constraints": {mood_constraints if mood_constraints else '[]'},
      "use_only_user_songs": {str(use_only_user_songs).lower()},
      "reference_track": <string or null>
    }}
    
    If the query explicitly states a number of songs (e.g., "give me 4 songs..."), set "explicit_song_count" to that number; otherwise, set it to null.
    If the query references a specific song (e.g., "like Halloween by Novo Amor"), set "reference_track" to that song's title; otherwise, set it to null.
    """
    if debug:
        reasoning.append("Constructed prompt for OpenAI API for extracting constraints.")
    try:
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": "Extract playlist constraints."},
                      {"role": "user", "content": prompt}],
            temperature=0.5
        )
        raw_content = response.choices[0].message.content.strip()
        if debug:
            reasoning.append(f"Received raw response from OpenAI: {raw_content}")
        raw_content = raw_content.strip("```json").strip("```").strip()
        extracted_data = json.loads(raw_content)
    except json.JSONDecodeError as e:
        if debug:
            reasoning.append(f"JSON decoding error: {e}. Falling back to defaults.")
        extracted_data = {}
    except Exception as e:
        if debug:
            reasoning.append(f"OpenAI API Error: {str(e)}. Falling back to defaults.")
        extracted_data = {}

    extracted_data.setdefault("duration_minutes", extracted_duration)
    extracted_data.setdefault("bpm_range", [60, 130])
    extracted_data.setdefault("genres", genres if genres else ["any"])
    extracted_data.setdefault("release_year_range", [2019, 2024])
    extracted_data.setdefault("mood_constraints", mood_constraints if mood_constraints else [])
    extracted_data.setdefault("use_only_user_songs", use_only_user_songs)
    extracted_data.setdefault("explicit_song_count", None)
    extracted_data.setdefault("reference_track", None)

    if debug:
        reasoning.append(f"Final extracted constraints: {extracted_data}")
        return extracted_data, reasoning
    else:
        return extracted_data

def matches_mood(song_mood, mood_constraints):
    if not mood_constraints or not song_mood:
        return True
    return any(mood.lower() in song_mood.lower() for mood in mood_constraints)

def validate_playlist(playlist, constraints, debug=False):
    validation_log = [] if debug else None
    header = "Validation Log:\n"
    header += f"User Query: '{constraints.get('user_query', 'N/A')}'\n"
    header += f"Extracted Duration: {constraints.get('duration_minutes')} minutes\n"
    header += f"BPM Range: {constraints.get('bpm_range')}\n"
    header += f"Genres: {constraints.get('genres')}\n"
    header += f"Mood Constraints: {constraints.get('mood_constraints')}\n"
    header += f"Personal songs only: {constraints.get('use_only_user_songs')}\n"
    validation_log.append(header)
    for song in playlist:
        msg = f"Song '{song['title']}' by {song['artist']}: "
        if "bpm" in song and song["bpm"] != "Unknown":
            bpm = song["bpm"]
            if constraints.get("bpm_range") and constraints["bpm_range"][0] <= bpm <= constraints["bpm_range"][1]:
                msg += f"BPM {bpm} is within the required range {constraints['bpm_range']}. "
            else:
                msg += f"BPM {bpm} is outside the required range {constraints['bpm_range']}. "
        else:
            msg += "BPM info is not available; cannot validate BPM. "
        if "source" in song and "reason" in song:
            msg += f"Source: {song['source']}. Reason: {song['reason']}. "
        else:
            msg += "No source/reason information provided. "
        if ENABLE_SONG_EXPLANATION:
            extra_explanation = explain_song_selection(song, constraints)
            msg += f"Explanation: {extra_explanation}"
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
            reasoning.append(f"Using explicit song count: {num_songs}")
    else:
        duration = constraints["duration_minutes"]
        avg_song_length = 4
        num_songs = max(5, round(duration / avg_song_length))
        if debug:
            reasoning.append(f"Calculated number of songs: {num_songs} based on duration {duration} minutes.")

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
        user_data = get_user_preferences(access_token=access_token)
        user_songs = user_data["liked_songs"] + user_data["top_tracks"]
        if reference_track:
            ref_details = get_reference_track_details(reference_track)
            if ref_details:
                reference_artist = ref_details.get("artist", "").strip().lower()
                for song in user_songs:
                    if song["artist"].strip().lower() == reference_artist:
                        song["source"] = "personal"
                        song["reason"] = f"Matches reference artist '{reference_artist}' from your library."
                        filtered_songs.append(song)
                if debug:
                    reasoning.append(f"Using personal library exclusively filtered by reference artist '{reference_artist}'; {len(filtered_songs)} songs found.")
            else:
                filtered_songs = user_songs
        else:
            filtered_songs = user_songs
        if debug:
            reasoning.append(f"Using personal library exclusively; {len(filtered_songs)} songs after filtering.")
    else:
        if reference_track:
            ref_details = get_reference_track_details(reference_track)
            if ref_details is not None:
                reference_track_name = ref_details.get("title")
                reference_artist = ref_details.get("artist")
                if debug:
                    reasoning.append(f"Found reference track details: '{reference_track_name}' by '{reference_artist}'.")
            else:
                reference_track_name = reference_track
                reference_artist = "Unknown"
                if debug:
                    reasoning.append("Could not find reference track details; using provided text.")
            external_recs = get_similar_tracks_lastfm(reference_track_name, reference_artist, limit=num_songs, debug=debug)
            if external_recs:
                for rec in external_recs:
                    rec["source"] = "Last.fm"
                    rec["reason"] = f"Recommended by Last.fm based on reference track '{reference_track_name}' by '{reference_artist}'."
                filtered_songs += external_recs
                if debug:
                    reasoning.append(f"Retrieved {len(external_recs)} recommendations from Last.fm based on reference track.")
            else:
                if debug:
                    reasoning.append("No recommendations returned from Last.fm; falling back to AI generation.")
    
    needed = num_songs - len(filtered_songs)
    if needed > 0:
        reference_line = ""
        if reference_track:
            reference_line = f"Reference track: {reference_track}. This track is known for its acoustic folk style, gentle vocals, introspective lyrics, and minimal production. Generate songs with similar characteristics."
        prompt = f"""
        Generate a playlist with the following constraints:
        - Genre: {genres}
        - BPM range: {bpm_start} to {bpm_end}
        - Release years: {release_year_range[0]} to {release_year_range[1]}
        - Mood constraints: {mood_constraints}
        - Number of songs: {needed}
        {reference_line}
        
        Respond in JSON format as a list of objects, each with keys "title", "artist", "bpm", and "release_year".
        """
        if debug:
            reasoning.append("Prompting AI for supplementary songs with the following prompt:")
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
                    song["reason"] = "Generated by AI to meet remaining song requirement based on constraints."
                filtered_songs += ai_songs
                if debug:
                    reasoning.append(f"AI generated {len(ai_songs)} songs, combined playlist now has {len(filtered_songs)} songs.")
            else:
                if debug:
                    reasoning.append("No valid JSON found in AI response; skipping AI supplementation.")
        except Exception as e:
            if debug:
                reasoning.append(f"OpenAI API Error during AI supplementation: {str(e)}")

    user_data = get_user_preferences(access_token=access_token)
    personal_tracks = {
        (song["name"].strip().lower(), song["artist"].strip().lower())
        for song in user_data["liked_songs"] + user_data["top_tracks"]
    }
    for song in filtered_songs:
        key = (song.get("title", "").strip().lower(), song.get("artist", "").strip().lower())
        if key in personal_tracks:
            song["liked"] = True

    playlist = [
        {"title": song.get("name", song.get("title")),
         "artist": song["artist"],
         "liked": song.get("liked", False),
         "mood": song.get("mood", "Unknown"),
         "source": song.get("source", "unknown"),
         "reason": song.get("reason", "No reason provided")}
        for song in filtered_songs[:num_songs]
    ]
    if debug:
        validation_log = validate_playlist(playlist, constraints, debug=debug)
        reasoning.append("Validation Log:")
        reasoning.extend(validation_log)
        return {"playlist": playlist, "reasoning": reasoning}
    else:
        return {"playlist": playlist}
