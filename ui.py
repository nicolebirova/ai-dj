#########################################################
## This file contains the UI interface implementation. ##
#########################################################
import streamlit as st
import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import os
import shutil
import urllib.parse

st.set_page_config(page_title="AI DJ - Playlist Generator", page_icon="🎵", layout="wide")
FASTAPI_URL = "https://ai-dj-o4qg.onrender.com"

SPOTIPY_CLIENT_ID = os.environ.get("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.environ.get("SPOTIPY_CLIENT_SECRET")
SPOTIPY_REDIRECT_URI = os.environ.get("SPOTIPY_REDIRECT_URI")
CACHE_PATH = ".spotify_caches"

if os.path.exists(CACHE_PATH):
    shutil.rmtree(CACHE_PATH)

sp_oauth = SpotifyOAuth(
    client_id=SPOTIPY_CLIENT_ID,
    client_secret=SPOTIPY_CLIENT_SECRET,
    redirect_uri=SPOTIPY_REDIRECT_URI,
    scope="user-library-read user-top-read playlist-modify-public playlist-modify-private",
    cache_path=f"{CACHE_PATH}/token_info.json"
)

for key in ["authenticated", "token_info", "user_info", "favorites_loaded", "playlist", "data_cached", "show_auth", "user_switched", "debug_info"]:
    if key not in st.session_state:
        if key in ["authenticated", "data_cached", "favorites_loaded", "user_switched"]:
            st.session_state[key] = False
        elif key in ["playlist", "debug_info"]:
            st.session_state[key] = []
        else:
            st.session_state[key] = None

if not st.session_state.authenticated:
    st.title("🎵 Welcome to Your AI DJ! 🎶")
    auth_url = sp_oauth.get_authorize_url()
    st.markdown(
        f'<a href="{auth_url}">'
        '<button style="background-color:#1DB954;color:white;padding:10px 20px;'
        'border:none;border-radius:5px;cursor:pointer;font-size:16px;">Login to Spotify</button></a>',
        unsafe_allow_html=True
    )
    st.subheader("Paste the redirected URL here:")
    redirected_url = st.text_input("Enter the URL after login:")
    if st.button("Authenticate"):
        if "code=" in redirected_url:
            try:
                code = redirected_url.split("code=")[-1].split("&")[0]
                token_info = sp_oauth.get_access_token(code)
                st.session_state.token_info = token_info
                st.session_state.authenticated = True
                st.session_state.user_switched = True
                sp = spotipy.Spotify(auth=token_info["access_token"])
                st.session_state.user_info = sp.current_user()
                st.success(f"✅ Logged in as {st.session_state.user_info['display_name']}!")
                st.session_state.data_cached = False
                st.rerun() 
            except Exception as e:
                st.error(f"❌ Authentication failed: {e}")
        else:
            st.error("❌ Invalid URL. Paste the full redirect URL after login.")

if st.session_state.authenticated:
    sp = spotipy.Spotify(auth=st.session_state.token_info["access_token"])
    st.session_state.user_info = sp.current_user()

    if not st.session_state.data_cached:
        with st.spinner("Loading your data... please wait (this may take a few minutes)"):
            access_token = st.session_state.token_info["access_token"]
            cache_response = requests.get(f"{FASTAPI_URL}/cache_user_data",
                                          params={"access_token": access_token, "debug": True})
            if cache_response.status_code == 200:
                st.session_state.data_cached = True
                st.success("Your liked songs have been preloaded!")
            else:
                st.error("Error preloading your data. Please try again.")
        st.rerun()

    st.sidebar.header("🎶 Your Favorites 🎧")
    if not st.session_state.favorites_loaded or st.session_state.user_switched:
        top_artists = sp.current_user_top_artists(limit=3)["items"]
        top_tracks = sp.current_user_top_tracks(limit=5)["items"]
        top_genres = list(set(genre for artist in top_artists for genre in artist["genres"]))
        st.session_state.favorites = {
            "top_artists": [artist["name"] for artist in top_artists],
            "top_tracks": [f"{track['name']} - {track['artists'][0]['name']}" for track in top_tracks],
            "top_genres": top_genres
        }
        st.session_state.favorites_loaded = True
        st.session_state.user_switched = False  
    st.sidebar.subheader(f"Hello, {st.session_state.user_info['display_name']}! 👋")
    st.sidebar.write("🎤 **Top Artists:**")
    for artist in st.session_state.favorites["top_artists"]:
        st.sidebar.write(f"✅ {artist}")
    st.sidebar.write("🎶 **Favorite Genres:**", ", ".join(st.session_state.favorites["top_genres"]))
    st.sidebar.write("📀 **Top Songs:**")
    for track in st.session_state.favorites["top_tracks"]:
        st.sidebar.write(f"🎵 {track}")

    st.title("🎵 AI DJ - Generate Your Playlist")
    user_query = st.text_input("Enter your playlist request 🎶:", "")
    debug_mode = st.checkbox("Show Reasoning (Debugging)", value=False)
    
    if st.button("Generate Playlist"):
        access_token = st.session_state.token_info["access_token"]
        response = requests.get(f"{FASTAPI_URL}/generate_playlist", 
                                params={"user_query": user_query, "debug": debug_mode, "access_token": access_token})
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and "playlist" in data:
                st.session_state.playlist = data["playlist"]
                st.session_state.debug_info = data.get("reasoning", [])
                st.rerun()
        else:
            st.error("Error generating playlist. Check API connection.")

if st.session_state.authenticated and st.session_state.playlist:
    st.subheader("🎵 Generated Playlist")
    total_duration = len(st.session_state.playlist) * 4  
    st.write(f"**Total Duration:** {total_duration} minutes")
    for i, song in enumerate(st.session_state.playlist):
        with st.container():
            col1, col2 = st.columns([1, 4])
            with col1:
                cover_url = song.get("album_cover", "https://via.placeholder.com/200")
                st.image(cover_url, use_container_width=True)
            with col2:
                st.write(f"**{song['title']}** - {song['artist']}")
                search_query = urllib.parse.quote(f"{song['title']} {song['artist']}")
                st.markdown(f"[▶️ Listen on Spotify](https://open.spotify.com/search/{search_query})")
    
    st.subheader("Save Your Playlist to Spotify")
    playlist_name = st.text_input("Enter a name for your playlist:")
    if st.button("Save Playlist"):
        track_uris = [song.get("uri") for song in st.session_state.playlist]
        params = {
            "playlist_name": playlist_name,
            "track_uris": track_uris,
            "access_token": st.session_state.token_info["access_token"]
        }
        save_resp = requests.get(f"{FASTAPI_URL}/save_playlist", params=params)
        if save_resp.status_code == 200:
            save_data = save_resp.json()
            st.success(f"Playlist '{playlist_name}' saved! You can view it here: {save_data.get('url')}")
        else:
            st.error("Error saving playlist. Please check your API connection.")
    
    if debug_mode and st.session_state.debug_info:
        with st.expander("Debugging Info - Chain-of-Thought Reasoning"):
            for line in st.session_state.debug_info:
                st.write(line)
    if st.button("Make a New Playlist Request"):
        st.session_state.playlist = []
        st.session_state.debug_info = []
        st.rerun()
