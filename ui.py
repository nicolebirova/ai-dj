import streamlit as st
import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import os
import shutil

st.set_page_config(page_title="AI DJ - Playlist Generator", page_icon="🎵", layout="wide")

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

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "token_info" not in st.session_state:
    st.session_state.token_info = None
if "user_info" not in st.session_state:
    st.session_state.user_info = None
if "favorites_loaded" not in st.session_state:
    st.session_state.favorites_loaded = False
if "playlist" not in st.session_state:
    st.session_state.playlist = None
if "album_covers" not in st.session_state:
    st.session_state.album_covers = []
if "show_auth" not in st.session_state:
    st.session_state.show_auth = False  

if not st.session_state.authenticated:
    st.title("🎵 Welcome to Your AI DJ! 🎶")

    auth_url = sp_oauth.get_authorize_url() 
    
    if st.button("Connect to your Spotify to get started!"):
        st.session_state.show_auth = True 
        st.rerun()

if st.session_state.show_auth and not st.session_state.authenticated:
    st.subheader("1️⃣ Click the link below to log in with Spotify:")
    st.markdown(f"[Login to Spotify]({auth_url})", unsafe_allow_html=True)

    st.subheader("2️⃣ Paste the redirected URL here:")
    redirected_url = st.text_input("Enter URL:")

    if st.button("Authenticate"):
        if "code=" in redirected_url:
            try:
                code = redirected_url.split("code=")[-1].split("&")[0]
                token_info = sp_oauth.get_access_token(code)

                st.session_state.token_info = token_info
                st.session_state.authenticated = True

                sp = spotipy.Spotify(auth=token_info["access_token"])
                st.session_state.user_info = sp.current_user()

                st.success(f"✅ Successfully authenticated as {st.session_state.user_info['display_name']}!")
                st.rerun()
            except Exception as e:
                st.error(f"❌ Authentication failed: {e}")
        else:
            st.error("❌ Invalid URL. Please paste the correct redirect URL after logging in.")

if st.session_state.authenticated:
    sp = spotipy.Spotify(auth=st.session_state.token_info["access_token"])
    st.session_state.user_info = sp.current_user()

    st.sidebar.header("🎶 Your Favorites 🎧")
    
    if not st.session_state.favorites_loaded:
        top_artists = sp.current_user_top_artists(limit=5)["items"]
        top_tracks = sp.current_user_top_tracks(limit=5)["items"]
        top_genres = list(set(genre for artist in top_artists for genre in artist["genres"]))

        st.session_state.favorites = {
            "top_artists": [artist["name"] for artist in top_artists],
            "top_tracks": [f"{track['name']} - {track['artists'][0]['name']}" for track in top_tracks],
            "top_genres": top_genres
        }
        st.session_state.favorites_loaded = True

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

    if st.button("Generate Playlist"):
        response = requests.get(f"http://127.0.0.1:8000/generate_playlist?user_query={user_query}")
        
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and "playlist" in data:
                st.session_state.playlist = data["playlist"]
                st.session_state.album_covers = []  

                for song in st.session_state.playlist:
                    search_result = sp.search(q=f"{song['title']} {song['artist']}", type="track", limit=1)
                    if search_result["tracks"]["items"]:
                        track = search_result["tracks"]["items"][0]
                        album_cover = track["album"]["images"][0]["url"] if track["album"]["images"] else None
                        st.session_state.album_covers.append(album_cover)
                    else:
                        st.session_state.album_covers.append(None)  

                st.rerun()
        else:
            st.error("Error generating playlist. Check API connection.")

if st.session_state.authenticated and st.session_state.playlist:
    st.subheader("🎵 Generated Playlist")
    total_duration = len(st.session_state.playlist) * 4  # Estimate total duration
    st.write(f"**Total Duration:** {total_duration} minutes")

    if st.button("Save to Spotify 🎶"):
        user_id = sp.current_user()["id"]
        playlist_name = f"AI DJ - {user_query[:30]}..."
        new_playlist = sp.user_playlist_create(user_id, playlist_name, public=True)

        track_uris = []
        for song in st.session_state.playlist:
            search_result = sp.search(q=f"{song['title']} {song['artist']}", type="track", limit=1)
            if search_result["tracks"]["items"]:
                track_uris.append(search_result["tracks"]["items"][0]["uri"])

        if track_uris:
            sp.user_playlist_add_tracks(user_id, new_playlist["id"], track_uris)
            st.success(f"Playlist saved! 🎉 [Listen here](https://open.spotify.com/playlist/{new_playlist['id']})")

    for i, song in enumerate(st.session_state.playlist):
        with st.container():
            col1, col2 = st.columns([1, 4])
            with col1:
                st.image(st.session_state.album_covers[i], use_column_width=True)
            with col2:
                st.write(f"**{song['title']}** - {song['artist']}")
                st.markdown(f"[▶️ Listen on Spotify](https://open.spotify.com/search/{song['title']} {song['artist']})")

    if st.button("Make a New Playlist Request"):
        st.session_state.playlist = None
        st.session_state.album_covers = []
        st.rerun()
