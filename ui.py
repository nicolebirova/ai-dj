import streamlit as st
import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import os
import shutil

st.set_page_config(page_title="AI DJ - Playlist Generator", page_icon="🎵", layout="wide")

SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
SPOTIPY_REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI")
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

for key in ["authenticated", "token_info", "user_info", "favorites_loaded", "playlist", "album_covers", "show_auth", "user_switched"]:
    if key not in st.session_state:
        st.session_state[key] = False if "authenticated" in key else None if "token_info" in key else []

if not st.session_state.authenticated:
    st.title("🎵 Welcome to Your AI DJ! 🎶")

    auth_url = sp_oauth.get_authorize_url()

    st.markdown(
        f'<a href="{auth_url}" target="_self">'
        '<button style="background-color:#1DB954;color:white;padding:10px 20px;'
        'border:none;border-radius:5px;cursor:pointer;font-size:16px;">Login to Spotify</button></a>',
        unsafe_allow_html=True
    )

    st.subheader("2️⃣ Paste the redirected URL here:")
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
                st.rerun()
            except Exception as e:
                st.error(f"❌ Authentication failed: {e}")
        else:
            st.error("❌ Invalid URL. Paste the full redirect URL after login.")

if st.session_state.authenticated:
    sp = spotipy.Spotify(auth=st.session_state.token_info["access_token"])
    st.session_state.user_info = sp.current_user()

    st.sidebar.header("🎶 Your Favorites 🎧")

    if not st.session_state.favorites_loaded or st.session_state.user_switched:
        top_artists = sp.current_user_top_artists(limit=5)["items"]
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
    total_duration = len(st.session_state.playlist) * 4  
    st.write(f"**Total Duration:** {total_duration} minutes")

    for i, song in enumerate(st.session_state.playlist):
        with st.container():
            col1, col2 = st.columns([1, 4])
            with col1:
                st.image(st.session_state.album_covers[i] if st.session_state.album_covers[i] else "https://via.placeholder.com/200", use_column_width=True)
            with col2:
                st.write(f"**{song['title']}** - {song['artist']}")
                st.markdown(f"[▶️ Listen on Spotify](https://open.spotify.com/search/{song['title']} {song['artist']})")

    if st.button("Make a New Playlist Request"):
        st.session_state.playlist = None
        st.session_state.album_covers = []
        st.rerun()
