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
from typing import List

load_dotenv()

openai.api_key = os.environ.get("OPENAI_API_KEY")

sp_oauth = SpotifyOAuth(
    client_id=os.environ.get("SPOTIPY_CLIENT_ID"),
    client_secret=os.environ.get("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.environ.get("SPOTIPY_REDIRECT_URI"),
    scope="user-top-read user-library-read playlist-modify-public"
)

app = FastAPI()

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
def get_user_data(access_token: str = Query(..., description="User's Spotify access token")):
    return get_user_preferences(access_token)

@app.get("/generate_playlist")
def generate_personalized_playlist(
    user_query: str = Query(..., description="Describe your playlist request"),
    access_token: str = Query(..., description="User's Spotify access token"),
    debug: bool = Query(False, description="Enable debug mode to show chain-of-thought reasoning")
):
    result = generate_constrained_playlist(user_query, access_token=access_token, debug=debug)
    return result


@app.get("/save_playlist")
def save_playlist(
    playlist_name: str, 
    track_uris: List[str], 
    access_token: str = Query(..., description="User's Spotify access token")
):
    sp = spotipy.Spotify(auth=access_token)
    user_id = sp.me()["id"]
    playlist = sp.user_playlist_create(user_id, playlist_name, public=True)
    sp.playlist_add_items(playlist["id"], track_uris)
    return {"message": f"Playlist '{playlist_name}' created!", "url": playlist["external_urls"]["spotify"]}
