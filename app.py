import streamlit as st
import urllib.request
import urllib.parse
import re
import json
import sqlite3
import asyncio
import edge_tts
import os
from deep_translator import GoogleTranslator
import time
import textwrap

# Page configuration
st.set_page_config(
    page_title="EduTube - Advanced Search",
    page_icon="🔍",
    layout="wide",
)

# --- CUSTOM CSS FOR LARGER FONT ---
st.markdown("""
<style>
    /* Increase general text size */
    html, body, [class*="st-"], .stMarkdown, p, div, span, label {
        font-size: 18px !important;
    }
    /* Make the clickable links even larger */
    a {
        font-size: 20px !important;
    }
</style>
""", unsafe_allow_html=True)

# --- DATABASE SETUP (Runs automatically) ---
def init_db():
    """Creates a local database file if it doesn't exist yet."""
    conn = sqlite3.connect("edutube_basic_library.db")
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS saved_videos (
            video_id TEXT PRIMARY KEY,
            title TEXT,
            url TEXT,
            duration TEXT,
            age TEXT
        )
    ''')
    # Safely upgrade existing databases to include the channel column
    try:
        c.execute("ALTER TABLE saved_videos ADD COLUMN channel TEXT DEFAULT 'Unknown Channel'")
    except sqlite3.OperationalError:
        pass # Column already exists
    conn.commit()
    conn.close()

init_db()

def save_video(video_id, title, channel, url, duration, age):
    conn = sqlite3.connect("edutube_basic_library.db")
    c = conn.cursor()
    try:
        c.execute("INSERT INTO saved_videos (video_id, title, channel, url, duration, age) VALUES (?, ?, ?, ?, ?, ?)", 
                 (video_id, title, channel, url, duration, age))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False # Video already exists
    finally:
        conn.close()

def get_saved_videos():
    conn = sqlite3.connect("edutube_basic_library.db")
    c = conn.cursor()
    c.execute("SELECT video_id, title, url, duration, age, channel FROM saved_videos")
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "title": r[1], "url": r[2], "duration": r[3], "age": r[4], "channel": r[5] or "Unknown Channel"} for r in rows]

def remove_video(video_id):
    conn = sqlite3.connect("edutube_basic_library.db")
    c = conn.cursor()
    c.execute("DELETE FROM saved_videos WHERE video_id=?", (video_id,))
    conn.commit()
    conn.close()

# --- INITIALIZE FIRST LOAD STATE ---
if "first_load" not in st.session_state:
    st.session_state["first_load"] = True
if "search_results" not in st.session_state:
    st.session_state["search_results"] = []

# --- HELPER FUNCTIONS FOR SORTING ---
def parse_duration(d_str):
    if not d_str or d_str == 'Unknown':
        return 0
    parts = d_str.split(':')
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        return int(parts[0])
    except:
        return 0

def parse_age(age_str):
    if not age_str or age_str == 'Unknown':
        return 999999
    text = age_str.lower()
    numbers = [int(s) for s in text.split() if s.isdigit()]
    val = numbers[0] if numbers else 1
    
    if 'minute' in text: return val / 1440
    if 'hour' in text: return val / 24
    if 'day' in text: return val
    if 'week' in text: return val * 7
    if 'month' in text: return val * 30
    if 'year' in text: return val * 365
    return 999999

st.title("🔍 EduTube: Advanced Keyword Search")

# --- CREATE TABS ---
tab_search, tab_library, tab_read_aloud = st.tabs(["🔍 Search Videos", "📚 My Saved Library", "🗣️ Translate & Read"])

# ==========================================
# TAB 1: SEARCH
# ==========================================
with tab_search:
    st.write("Select all your filters below (including Shorts!), then click **Apply & Search** to load the results.")
    
    with st.form("search_form"):
        col1, col2 = st.columns([3, 1])
        with col1:
            user_prompt = st.text_input(
                "Enter your search keyword (add language here if needed):",
                value="World news, podcasts, inventions, world markets, Indian markets, Indian News",
                placeholder="e.g., Podcasts on science...",
            )
        with col2:
            num_videos = st.number_input("Videos to list:", min_value=1, max_value=500, value=15)

        st.markdown("#### Search Filters")
        col3, col4, col5 = st.columns(3)
        with col3:
            search_scope = st.radio("Search Filter:", ["Educational Only", "Any Content"], index=1, horizontal=True)
            video_type = st.radio(
                "Video Type:", 
                ["Standard Videos Only", "Include Shorts", "Shorts Only"], 
                index=0, 
                horizontal=True
            )
        with col4:
            display_style = st.radio("Display Style:", ["Just Clickable Links", "Video Players"], index=0, horizontal=True)
        with col5:
            sort_by = st.selectbox(
                "Sort Results By:",
                ["Default (Relevance)", "Duration (Short to Long)", "Duration (Long to Short)", "Newest First"],
                index=3
            )
        
        st.markdown("---")
        submit_button = st.form_submit_button(label="Apply & Search")

    # --- MAIN SEARCH LOGIC ---
    if submit_button or st.session_state["first_load"]:
        if st.session_state["first_load"]:
            st.session_state["first_load"] = False

        if user_prompt:
            st.subheader(f"Results for: '{user_prompt}'")
            
            with st.spinner("Fetching requested videos..."):
                try:
                    search_term = user_prompt + " educational" if search_scope == "Educational Only" else user_prompt
                    query_string = urllib.parse.urlencode({"search_query": search_term})
                    url = "https://www.youtube.com/results?" + query_string
                    
                    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                    html_content = urllib.request.urlopen(req).read().decode('utf-8')
                    
                    json_match = re.search(r'var ytInitialData = ({.*?});</script>', html_content)
                    
                    if json_match:
                        data = json.loads(json_match.group(1))
                        videos = []
                        
                        try:
                            contents = data['contents']['twoColumnSearchResultsRenderer']['primaryContents']['sectionListRenderer']['contents'][0]['itemSectionRenderer']['contents']
                            for item in contents:
                                if 'videoRenderer' in item:
                                    vid = item['videoRenderer']
                                    
                                    v_id = vid.get('videoId', '')
                                    raw_title = vid.get('title', {}).get('runs', [{}])[0].get('text', 'No Title')
                                    v_duration = vid.get('lengthText', {}).get('simpleText', 'Unknown')
                                    v_age = vid.get('publishedTimeText', {}).get('simpleText', 'Unknown')
                                    
                                    # Extract Channel Name
                                    owner_data = vid.get('shortBylineText') or vid.get('ownerText') or vid.get('longBylineText') or {}
                                    v_channel = owner_data.get('runs', [{}])[0].get('text', 'Unknown Channel')
                                    
                                    duration_sec = parse_duration(v_duration)
                                    is_short = (0 < duration_sec <= 65)
                                    
                                    if video_type == "Standard Videos Only" and is_short:
                                        continue
                                    if video_type == "Shorts Only" and not is_short:
                                        continue
                                        
                                    badge = "📱 [SHORT] " if is_short else ""
                                    v_title = badge + raw_title
                                    
                                    if v_id:
                                        videos.append({
                                            'id': v_id,
                                            'title': v_title,
                                            'channel': v_channel,
                                            'duration': v_duration,
                                            'duration_sec': duration_sec,
                                            'age': v_age,
                                            'age_days': parse_age(v_age),
                                            'url': f"https://www.youtube.com/watch?v={v_id}"
                                        })
                        except Exception as e:
                            pass 
                        
                        if sort_by == "Duration (Short to Long)":
                            videos.sort(key=lambda x: x['duration_sec'])
                        elif sort_by == "Duration (Long to Short)":
                            videos.sort(key=lambda x: x['duration_sec'], reverse=True)
                        elif sort_by == "Newest First":
                            videos.sort(key=lambda x: x['age_days'])
                            
                        videos = videos[:num_videos]
                        st.session_state["search_results"] = videos
                        
                    else:
                        st.error("Failed to parse YouTube data. They may have updated their site layout.")
                        
                except Exception as e:
                    st.error(f"Search failed. Please check your internet connection. (Error: {e})")

        else:
            st.warning("Please type a keyword in the box before clicking Apply & Search.")

    # --- RENDER SEARCH RESULTS ---
    if st.session_state["search_results"]:
        if display_style == "Just Clickable Links":
            for v in st.session_state["search_results"]:
                col_text, col_btn = st.columns([5, 1])
                with col_text:
                    st.markdown(
                        f"🔗 **[{v['title']}]({v['url']})**<br>"
                        f"<span style='color:gray; font-size:0.9em;'>📺 {v['channel']} | ⏱️ {v['duration']} | 📅 {v['age']}</span>",
                        unsafe_allow_html=True
                    )
                with col_btn:
                    if st.button("⭐ Save", key=f"save_{v['id']}"):
                        success = save_video(v['id'], v['title'], v['channel'], v['url'], v['duration'], v['age'])
                        if success:
                            st.toast(f"Saved: {v['title'][:30]}...")
                        else:
                            st.toast("Already in library!", icon="⚠️")
                st.markdown("<br>", unsafe_allow_html=True)
        else:
            cols = st.columns(2)
            for index, v in enumerate(st.session_state["search_results"]):
                with cols[index % 2]:
                    st.markdown(f"**{v['title']}**")
                    st.caption(f"📺 {v['channel']} | ⏱️ {v['duration']} | 📅 {v['age']}")
                    
                    if st.button("⭐ Save to Library", key=f"save_vid_{v['id']}"):
                        success = save_video(v['id'], v['title'], v['channel'], v['url'], v['duration'], v['age'])
                        if success:
                            st.toast("Saved to your Library!")
                        else:
                            st.toast("Already in library!", icon="⚠️")
                            
                    st.video(v['url'])
                    st.markdown("---")

# ==========================================
# TAB 2: MY LIBRARY
# ==========================================
with tab_library:
    st.subheader("📚 Your Saved Videos")
    
    saved_videos = get_saved_videos()
    
    if not saved_videos:
        st.info("Your library is empty. Go to the Search tab and click '⭐ Save' on some videos!")
    else:
        st.write(f"You have **{len(saved_videos)}** videos saved.")
        
        lib_cols = st.columns(2)
        for index, v in enumerate(saved_videos):
            with lib_cols[index % 2]:
                st.markdown(f"**{v['title']}**")
                st.caption(f"📺 {v['channel']} | ⏱️ {v['duration']} | 📅 {v['age']}")
                
                # Delete button
                if st.button("❌ Remove", key=f"del_{v['id']}"):
                    remove_video(v['id'])
                    st.rerun() # Refresh the page instantly
                    
                st.video(v['url'])
                st.markdown("---")

# ==========================================
# TAB 3: READ ALOUD & TRANSLATION
# ==========================================
with tab_read_aloud:
    st.subheader("🗣️ Translate & Read Aloud")
    
    # Translation options mapped to deep-translator language codes
    translation_targets = {
        "No Translation (Read as is)": None,
        "English": "en",
        "Hindi": "hi",
        "Marathi": "mr"
    }
    
    # Voice options
    voice_options = {
        "English (US - Christopher)": "en-US-ChristopherNeural",
        "English (US - Aria)": "en-US-AriaNeural",
        "Marathi (Aarohi)": "mr-IN-AarohiNeural",
        "Hindi (Swara)": "hi-IN-SwaraNeural"
    }
    
    text_to_read = st.text_area("Text Box", height=200, placeholder="Paste text here...")
    
    col1, col2 = st.columns(2)
    with col1:
        selected_lang_label = st.selectbox("Translate to:", list(translation_targets.keys()))
        target_lang_code = translation_targets[selected_lang_label]
    with col2:
        selected_voice_label = st.selectbox("Select Voice Engine:", list(voice_options.keys()))
        selected_voice_code = voice_options[selected_voice_label]
    
    if st.button("▶️ Process & Play"):
        if text_to_read.strip():
            with st.spinner("Processing..."):
                final_text = text_to_read
                
                # 1. Translate if a target language is selected (Using translate_batch)
                if target_lang_code:
                    try:
                        # Break text into safe chunks
                        chunks = textwrap.wrap(text_to_read, width=1000, replace_whitespace=False)
                        
                        # Initialize translator and use the built-in batch processing
                        translator = GoogleTranslator(source='auto', target=target_lang_code)
                        translated_chunks = translator.translate_batch(chunks)
                        
                        # Rejoin the translated chunks, ignoring any empty results
                        final_text = " ".join([chunk for chunk in translated_chunks if chunk])
                        
                        st.success("Translated Text:")
                        st.write(final_text)
                    except Exception as e:
                        st.error(f"Translation failed: {e}")
                        final_text = ""
                
                # 2. Generate Audio
                if final_text:
                    audio_file = "temp_streamlit_audio.mp3"
                    
                    async def generate_audio():
                        communicate = edge_tts.Communicate(final_text, selected_voice_code)
                        await communicate.save(audio_file)
                    
                    try:
                        asyncio.run(generate_audio())
                        st.audio(audio_file, format="audio/mp3", autoplay=True)
                    except Exception as e:
                        st.error(f"Failed to generate audio: {e}")
        else:
            st.warning("Please paste some text in the box first.")
