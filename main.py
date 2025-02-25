################################################
## This file contains the FastAPI Application ##
################################################

import os
import openai
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from fastapi import FastAPI, Request, Query
from fastapi.responses import RedirectResponse
from dotenv import load_dotenv
import json
import re
from music_utils import get_user_preferences, get_song_metadata, interpret_user_query, generate_constrained_playlist

# Load environment variables
load_dotenv()

# Set OpenAI API Key
openai.api_key = os.getenv("OPENAI_API_KEY")

# Spotify OAuth Setup
sp_oauth = SpotifyOAuth(
    client_id=os.getenv("SPOTIPY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
    scope="user-top-read user-library-read playlist-modify-public"
)

app = FastAPI()

# Authentication & User Preferences
@app.get("/")
def home():
    return {"message": "Welcome to AI DJ! Please log in to Spotify."}

@app.get("/login")
def login():
    auth_url = sp_oauth.get_authorize_url()
    return RedirectResponse(auth_url)

@app.get("/callback")
def callback(request: Request):
    code = request.query_params.get("code")
    token_info = sp_oauth.get_access_token(code)
    return {"access_token": token_info["access_token"], "refresh_token": token_info["refresh_token"]}

@app.get("/get_user_data")
def get_user_data():
    return get_user_preferences()

# AI-Based Playlist Generation
@app.get("/generate_playlist")
def generate_personalized_playlist(user_query: str = Query(..., description="Describe your playlist request")):
    playlist = generate_constrained_playlist(user_query)
    return playlist

# Save Playlist to Spotify
@app.get("/save_playlist")
def save_playlist(playlist_name: str, track_uris: list):
    sp = spotipy.Spotify(auth_manager=sp_oauth)
    user_id = sp.me()["id"]
    playlist = sp.user_playlist_create(user_id, playlist_name, public=True)
    sp.playlist_add_items(playlist["id"], track_uris)
    return {"message": f"Playlist '{playlist_name}' created!", "url": playlist["external_urls"]["spotify"]}
