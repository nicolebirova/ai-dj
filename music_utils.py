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

# Load environment variables
load_dotenv()

# Spotify OAuth Setup
sp_oauth = SpotifyOAuth(
    client_id=os.environ.get("SPOTIPY_CLIENT_ID"),
    client_secret=os.environ.get("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.environ.get("SPOTIPY_REDIRECT_URI"),
    scope="user-top-read user-library-read"
)

# User Preferences
def get_user_preferences():
    
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

# Query Understanding
def interpret_user_query(user_query):
    
    duration_match = re.search(r"(\d+(\.\d+)?)\s*(hour|hr|min|minutes)", user_query, re.IGNORECASE)
    extracted_duration = None
    if duration_match:
        duration_value = float(duration_match.group(1))
        extracted_duration = int(duration_value * 60) if "hour" in duration_match.group(3).lower() else int(duration_value)

    if extracted_duration is None:
        if "car ride" in user_query.lower():
            extracted_duration = 45  
        elif "workout" in user_query.lower():
            extracted_duration = 60  
        elif "study session" in user_query.lower():
            extracted_duration = 90  

    genres, mood_constraints = [], []
    
    if "movie" in user_query.lower() or "soundtrack" in user_query.lower() or "cinematic" in user_query.lower():
        genres = ["orchestral", "cinematic", "electronic", "synthwave", "ambient"]
        mood_constraints = ["epic", "dramatic", "adventurous"]
    elif "relax" in user_query.lower() or "stress" in user_query.lower():
        genres = ["lofi", "chill", "ambient", "soft rock", "indie"]
        mood_constraints = ["calm", "peaceful", "soothing"]

    use_only_user_songs = any(
        term in user_query.lower() for term in ["only my liked songs", "only my favorites", "only my top tracks", "only my favourite songs"]
    )

    extracted_data = {
        "duration_minutes": extracted_duration if extracted_duration else 60,
        "bpm_range": [60, 130],
        "genres": genres if genres else ["any"],
        "release_year_range": [2019, 2024],
        "mood_constraints": mood_constraints if mood_constraints else [],
        "use_only_user_songs": use_only_user_songs
    }

    print("✅ Final Extracted Data:", extracted_data)
    return extracted_data

# Generate Playlist
def generate_constrained_playlist(user_query):
    constraints = interpret_user_query(user_query)

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
    elif isinstance(bpm_range, list) and len(bpm_range) == 2:
        bpm_start, bpm_end = bpm_range
    else:
        bpm_start, bpm_end = 60, 130  

    print(f"🎵 Generating a {duration}-minute playlist | BPM {bpm_start} → {bpm_end} | Genres: {genres} | Years: {release_year_range}")

    avg_song_length = 4  
    num_songs = max(5, round(duration / avg_song_length))  

    bpm_step = (bpm_end - bpm_start) / max(1, num_songs - 1) if gradual_increase else 0

    user_data = get_user_preferences()
    liked_songs_set = {(song["name"].lower(), song["artist"].lower()) for song in user_data["liked_songs"]}
    user_songs = []

    if any(term in user_query.lower() for term in ["liked songs", "my favorites", "favorite", "favourites"]):
        user_songs = user_data["liked_songs"] + user_data["top_tracks"]

    elif "top tracks" in user_query.lower():
        user_songs = user_data["top_tracks"]

    if use_only_user_songs:
        if user_songs:
            filtered_songs = user_songs
            if genres and "any" not in genres:
                filtered_songs = [song for song in user_songs if any(genre in genres for genre in user_data["top_genres"])]

            playlist = [{"title": song["name"], "artist": song["artist"], "liked": True} for song in filtered_songs[:num_songs]]

            print(f"✅ Returning ONLY user songs ({len(playlist)}/{num_songs} requested)")
            return {"playlist": playlist}
        else:
            print("❌ No user songs found. Cannot generate a playlist with ONLY user songs.")
            return {"error": "You requested only your songs, but no matching songs were found."}

    if user_songs:
        playlist = [{"title": song["name"], "artist": song["artist"], "liked": True} for song in user_songs[:num_songs]]
        print(f"✅ Using user songs ({len(playlist)}/{num_songs} requested)")
    else:
        prompt = f"""
        Generate a playlist with the following constraints:
        - Genres: {genres}
        - BPM range: {bpm_start} to {bpm_end} ({'gradual increase' if gradual_increase else 'fixed range'})
        - Release years: {release_year_range[0]} to {release_year_range[1]}
        - Mood constraints: {mood_constraints}
        - use only user songs: {use_only_user_songs}
        - **Duration:** {duration} min (~{num_songs} songs)

        🎬 **Ensure BPM follows the correct order if gradual increase is requested.**

        Respond in JSON format:
        [
            {{"title": "REAL SONG", "artist": "REAL ARTIST", "bpm": {bpm_start}, "release_year": {release_year_range[0]}, "mood": "matching mood"}},
            ...
        ]
        """

        try:
            response = openai.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "system", "content": "You are a music expert AI that generates playlists."},
                          {"role": "user", "content": prompt}],
                temperature=0.7
            )

            raw_content = response.choices[0].message.content
            print("🔍 OpenAI Raw Response:", raw_content)

            json_part = re.search(r"\[\s*{.*}\s*\]", raw_content, re.DOTALL)
            if json_part:
                playlist = json.loads(json_part.group(0))

                if gradual_increase:
                    playlist = sorted(playlist, key=lambda x: x["bpm"])

                for song in playlist:
                    song["liked"] = (song["title"].lower(), song["artist"].lower()) in liked_songs_set

                playlist = playlist[:num_songs]
                print(f"✅ Using AI-generated songs ({len(playlist)}/{num_songs} requested)")

            else:
                print("No valid JSON found in OpenAI response")
                return {"error": "Failed to generate playlist"}

        except Exception as e:
            print("OpenAI API Error:", str(e))
            return {"error": "Failed to generate playlist"}

    return {"playlist": playlist}
