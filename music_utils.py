##############################################################################
## This file contains helper functions (metadata, filtering, external APIs) ##
##############################################################################
import spotipy
import requests
import openai
import os
import json
import re
import pandas as pd
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

load_dotenv()

sp_oauth = SpotifyOAuth(
    client_id=os.environ.get("SPOTIPY_CLIENT_ID"),
    client_secret=os.environ.get("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.environ.get("SPOTIPY_REDIRECT_URI"),
    scope="user-top-read user-library-read"
)

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

# Helper Function 
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

# Song Metadata (BPM, Mood)
def get_song_metadata(track_name, artist_name):
    query_url = f"https://acousticbrainz.org/api/v1/{track_name} - {artist_name}/low-level"
    response = requests.get(query_url)
    if response.status_code == 200:
        data = response.json()
        bpm = data.get("rhythm", {}).get("bpm", "Unknown")
        mood = data.get("highlevel", {}).get("mood_acoustic", {}).get("value", "Unknown")
        return {"bpm": bpm, "mood": mood}
    return {"bpm": "Unknown", "mood": "Unknown"}

def interpret_user_query(user_query, debug=False):
    reasoning = [] if debug else None

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
        if "car ride" in user_query.lower():
            extracted_duration = 45  
            if debug:
                reasoning.append("Defaulting duration to 45 minutes for car ride.")
        elif "workout" in user_query.lower():
            extracted_duration = 60  
            if debug:
                reasoning.append("Defaulting duration to 60 minutes for workout.")
        elif "study session" in user_query.lower():
            extracted_duration = 90  
            if debug:
                reasoning.append("Defaulting duration to 90 minutes for study session.")
        else:
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

    use_only_user_songs = any(
        term in user_query.lower() for term in ["only my liked songs", "only my favorites", "only my top tracks", "only my favourite songs"]
    )
    if debug:
        if use_only_user_songs:
            reasoning.append("User requested to use only their own songs.")
        else:
            reasoning.append("User did not restrict to only their own songs.")

    prompt = f"""
    Extract structured playlist constraints from the following user request:
    "{user_query}"

    Response must be valid JSON:
    {{
      "duration_minutes": {extracted_duration if extracted_duration else 60},
      "bpm_range": [lowest BPM, highest BPM] (default: [60, 130]),
      "genres": {genres if genres else '["any"]'},
      "release_year_range": [last 5 years] (default if unspecified),
      "mood_constraints": {mood_constraints if mood_constraints else '[]'},
      "use_only_user_songs": {str(use_only_user_songs).lower()}
    }}
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

    extracted_data.setdefault("duration_minutes", extracted_duration if extracted_duration else 60)
    extracted_data.setdefault("bpm_range", [60, 130])
    extracted_data.setdefault("genres", genres if genres else ["any"])
    extracted_data.setdefault("release_year_range", [2019, 2024])
    extracted_data.setdefault("mood_constraints", mood_constraints if mood_constraints else [])
    extracted_data.setdefault("use_only_user_songs", use_only_user_songs)

    if debug:
        reasoning.append(f"Final extracted constraints: {extracted_data}")
        return extracted_data, reasoning
    else:
        return extracted_data

def validate_playlist(playlist, constraints, debug=False):
    validation_log = [] if debug else None
    bpm_range = constraints.get("bpm_range", [60, 130])
    for song in playlist:
        if "bpm" in song:
            bpm = song["bpm"]
            if bpm_range[0] <= bpm <= bpm_range[1]:
                if debug:
                    validation_log.append(f"Song '{song['title']}' BPM {bpm} is within range {bpm_range}.")
            else:
                if debug:
                    validation_log.append(f"Song '{song['title']}' BPM {bpm} is OUTSIDE range {bpm_range}.")
        else:
            if debug:
                validation_log.append(f"Song '{song['title']}' does not have BPM info; skipping BPM check.")
    return validation_log

def generate_constrained_playlist(user_query, access_token=None, debug=False):
    if debug:
        constraints, reasoning = interpret_user_query(user_query, debug=debug)
    else:
        constraints = interpret_user_query(user_query, debug=debug)
        reasoning = None

    duration = constraints["duration_minutes"]
    bpm_range = constraints["bpm_range"]
    genres = constraints["genres"]
    release_year_range = constraints["release_year_range"]
    mood_constraints = constraints["mood_constraints"]
    use_only_user_songs = constraints["use_only_user_songs"]

    gradual_increase = False
    if isinstance(bpm_range, dict) and bpm_range.get("gradual_increase"):
        gradual_increase = True
        bpm_start, bpm_end = bpm_range["start"], bpm_range["end"]
        if debug:
            reasoning.append("Using gradual BPM increase from dictionary specification.")
    elif isinstance(bpm_range, list) and len(bpm_range) == 2:
        bpm_start, bpm_end = bpm_range
        if debug:
            reasoning.append(f"Using fixed BPM range: {bpm_start} to {bpm_end}.")
    else:
        bpm_start, bpm_end = 60, 130
        if debug:
            reasoning.append("Fallback to default BPM range: 60 to 130.")

    if debug:
        reasoning.append(f"Generating a {duration}-minute playlist with BPM progression from {bpm_start} to {bpm_end}, genres {genres}, release years {release_year_range}, and mood constraints {mood_constraints}.")

    avg_song_length = 4
    num_songs = max(5, round(duration / avg_song_length))
    if debug:
        reasoning.append(f"Calculated number of songs: {num_songs} (assuming average song length of {avg_song_length} minutes).")

    bpm_step = (bpm_end - bpm_start) / max(1, num_songs - 1) if gradual_increase else 0

    user_data = get_user_preferences(access_token=access_token)
    user_songs = user_data["liked_songs"] + user_data["top_tracks"]

    def matches_mood(song_mood):
        if not mood_constraints or not song_mood:
            return True
        return any(mood.lower() in song_mood.lower() for mood in mood_constraints)

    filtered_songs = [song for song in user_songs if matches_mood(song.get("mood", ""))]

    if genres and "any" not in genres:
        filtered_songs = [song for song in filtered_songs if song_matches_genre(song, genres)]
        if debug:
            reasoning.append(f"After genre filtering, {len(filtered_songs)} user songs remain.")

    if not use_only_user_songs and len(filtered_songs) < num_songs:
        needed = num_songs - len(filtered_songs)
        if debug:
            reasoning.append(f"User songs are insufficient ({len(filtered_songs)} available). Generating {needed} additional songs using AI.")
        prompt = f"""
        Generate a playlist with the following constraints:
        - Genre: {genres}
        - BPM range: {bpm_start} to {bpm_end}
        - Release years: {release_year_range[0]} to {release_year_range[1]}
        - Mood constraints: {mood_constraints}
        - **Duration:** {needed * avg_song_length} minutes (approximately {needed} songs)
        
        Respond in JSON format as a list of objects, each with keys "title", "artist", "bpm", and "release_year".
        """
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
            if debug:
                reasoning.append(f"AI-generated response: {raw_content}")
            json_part = re.search(r"\[\s*{.*}\s*\]", raw_content, re.DOTALL)
            if json_part:
                ai_songs = json.loads(json_part.group(0))
                for song in ai_songs:
                    song["liked"] = False
                filtered_songs = filtered_songs + ai_songs
                if debug:
                    reasoning.append(f"Combined playlist now has {len(filtered_songs)} songs after AI supplementation.")
            else:
                if debug:
                    reasoning.append("No valid JSON found in AI response; skipping AI supplementation.")
        except Exception as e:
            if debug:
                reasoning.append(f"OpenAI API Error during AI supplementation: {str(e)}")

    playlist = [{"title": song["name"] if "name" in song else song["title"],
                 "artist": song["artist"],
                 "liked": song.get("liked", False),
                 "mood": song.get("mood", "Unknown")} for song in filtered_songs[:num_songs]]
    if debug:
        reasoning.append(f"Final playlist constructed with {len(playlist)} songs.")

    if debug:
        validation_log = validate_playlist(playlist, constraints, debug=debug)
        reasoning.append("Validation Log:")
        reasoning.extend(validation_log)

    if debug:
        return {"playlist": playlist, "reasoning": reasoning}
    else:
        return {"playlist": playlist}
