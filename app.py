import streamlit as st
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import google.generativeai as genai
import pandas as pd
import isodate
from datetime import datetime, timedelta # Library waktu

# --- KONFIGURASI HALAMAN ---
st.set_page_config(
    page_title="YouTube Trend Intelligence Pro",
    page_icon="📹",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- FUNGSI HELPER & LOGIKA DATA ---

def parse_duration(duration_str):
    """Mengubah format durasi YouTube (PT1H2M10S) ke detik."""
    try:
        if not duration_str: return 0
        duration = isodate.parse_duration(duration_str)
        return duration.total_seconds()
    except:
        return 0

def get_channel_stats(youtube, channel_ids):
    """Mengambil data subscriber untuk banyak channel sekaligus."""
    try:
        unique_ids = list(set(channel_ids))
        stats = {}
        # Batching 50 ID per request
        for i in range(0, len(unique_ids), 50):
            chunk = unique_ids[i:i+50]
            if not chunk: continue
            
            request = youtube.channels().list(
                part="statistics",
                id=','.join(chunk)
            )
            response = request.execute()
            
            for item in response.get('items', []):
                sub_count = int(item['statistics'].get('subscriberCount', 0))
                stats[item['id']] = sub_count
        return stats
    except Exception:
        return {}

def process_video_items(items, youtube_service=None):
    """Mengolah JSON mentah menjadi DataFrame lengkap dengan Subscriber."""
    data = []
    if not items: return pd.DataFrame()
    
    # 1. Kumpulkan Channel ID
    channel_ids = []
    for item in items:
        snippet = item.get('snippet', {})
        c_id = snippet.get('channelId')
        if c_id: channel_ids.append(c_id)
            
    # 2. Ambil Data Subscriber
    channel_stats = {}
    if youtube_service and channel_ids:
        channel_stats = get_channel_stats(youtube_service, channel_ids)
    
    # 3. Gabungkan Data
    for item in items:
        video_id = item['id'] if isinstance(item['id'], str) else item['id'].get('videoId')
        if not video_id: continue
            
        snippet = item.get('snippet', {})
        statistics = item.get('statistics', {})
        content_details = item.get('contentDetails', {})
        
        view_count = int(statistics.get('viewCount', 0))
        like_count = int(statistics.get('likeCount', 0))
        comment_count = int(statistics.get('commentCount', 0))
        engagement = ((like_count + comment_count) / view_count * 100) if view_count > 0 else 0
        
        duration_sec = parse_duration(content_details.get('duration', 'PT0S'))
        
        ch_id = snippet.get('channelId')
        subs_count = channel_stats.get(ch_id, 0)
        
        data.append({
            'Video ID': video_id,
            'Title': snippet.get('title', ''),
            'Channel': snippet.get('channelTitle', ''),
            'Subscribers': subs_count,
            'Views': view_count,
            'Likes': like_count,
            'Comments': comment_count,
            'Engagement (%)': round(engagement, 2),
            'Duration (Min)': round(duration_sec / 60, 2),
            'Publish Date': snippet.get('publishedAt', '').split('T')[0],
            'Thumbnail': snippet.get('thumbnails', {}).get('high', {}).get('url', ''),
            'Tags': snippet.get('tags', []),
            'URL': f"https://www.youtube.com/watch?v={video_id}"
        })
    
    return pd.DataFrame(data)

# --- FUNGSI AI GENERATOR (AUTO-DETECT) ---
def generate_ai_strategy(api_key, video_data, topic):
    """Mengirim data trending ke Gemini dengan Auto-Detect Model."""
    try:
        genai.configure(api_key=api_key)
        
        # 1. Cek model tersedia
        available_models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_models.append(m.name)
        
        # 2. Pilih prioritas
        selected_model = ""
        priorities = ['models/gemini-1.5-flash', 'models/gemini-pro', 'models/gemini-1.0-pro']
        for p in priorities:
            if p in available_models:
                selected_model = p
                break
        
        if not selected_model and available_models:
            selected_model = available_models[0]
            
        if not selected_model:
            return "❌ Tidak ada model AI yang aktif."

        # 3. Generate
        model = genai.GenerativeModel(selected_model)
        
        data_text = ""
        for i, vid in enumerate(video_data[:5]): 
            data_text += f"{i+1}. {vid['Title']} (Views: {vid['Views']}, Channel: {vid['Channel']})\n"
            
        prompt = f"""
        Saya YouTuber pemula niche '{topic}'.
        Data kompetitor trending:
        {data_text}
        
        Berikan saran strategi:
        1. 3 Ide Judul Video Baru (Pola ATM).
        2. Analisis Singkat: Kenapa video ini laku?
        3. Saran Thumbnail: Warna & Objek.
        
        Jawab Bahasa Indonesia santai & Markdown.
        """
        
        response = model.generate_content(prompt)
        return f"**Model AI: {selected_model}**\n\n" + response.text
        
    except Exception as e:
        return f"❌ Error AI: {str(e)}"

# --- FUNGSI API YOUTUBE (DENGAN FILTER) ---

def get_youtube_service(api_key):
    try:
        return build('youtube', 'v3', developerKey=api_key)
    except Exception:
        return None

def validate_api_key(api_service):
    try:
        request = api_service.videos().list(part="id", chart="mostPopular", regionCode="ID", maxResults=1)
        request.execute()
        return True
    except HttpError as e:
        if e.resp.status == 403: return "QUOTA_EXCEEDED"
        return "INVALID_KEY"
    except Exception:
        return "ERROR"

@st.cache_data(show_spinner=False)
def get_trending_videos(api_key, region_code="ID", max_results=50):
    youtube = build('youtube', 'v3', developerKey=api_key)
    try:
        request = youtube.videos().list(
            part="snippet,contentDetails,statistics",
            chart="mostPopular",
            regionCode=region_code,
            maxResults=max_results
        )
        response = request.execute()
        return process_video_items(response.get('items', []), youtube_service=youtube) 
    except Exception as e:
        st.error(f"Error Trending: {e}")
        return pd.DataFrame()

@st.cache_data(show_spinner=False)
def search_videos_niche(api_key, query, region_code="ID", max_results=50, published_after=None):
    youtube = build('youtube', 'v3', developerKey=api_key)
    try:
        search_params = {
            "part": "id",
            "q": query,
            "type": "video",
            "regionCode": region_code,
            "maxResults": max_results
        }
        if published_after:
            search_params["publishedAfter"] = published_after

        # Step 1: Search IDs
        search_request = youtube.search().list(**search_params)
        search_response = search_request.execute()
        video_ids = [item['id']['videoId'] for item in search_response.get('items', [])]
        
        if not video_ids: return pd.DataFrame()
            
        # Step 2: Details
        videos_request = youtube.videos().list(part="snippet,contentDetails,statistics", id=','.join(video_ids))
        videos_response = videos_request.execute()
        
        return process_video_items(videos_response.get('items', []), youtube_service=youtube)
    except Exception as e:
        st.error(f"Error Search: {e}")
        return pd.DataFrame()

# --- SIDEBAR CONTROL ---
with st.sidebar:
    st.header("⚙️ Konfigurasi")
    
    # KEYS
    st.markdown("**1. YouTube Key**")
    if 'api_key' not in st.session_state: st.session_state.api_key = ''
    api_key_input = st.text_input("YouTube API Key", type="password", value=st.session_state.api_key, key="yt_input")
    
    if api_key_input:
        st.session_state.api_key = api_key_input
        youtube = get_youtube_service(api_key_input)
        if st.button("Validasi Key"):
            res = validate_api_key(youtube)
            if res == True:
                st.success("✅ Terhubung!")
                st.session_state.is_valid = True
            else:
                st.error("❌ Key Bermasalah")
                st.session_state.is_valid = False

    st.markdown("**2. Gemini AI Key (Opsional)**")
    if 'gemini_key' not in st.session_state: st.session_state.gemini_key = ''
    gemini_key_input = st.text_input("Gemini Key", type="password", value=st.session_state.gemini_key, key="ai_input")
    if gemini_key_input: st.session_state.gemini_key = gemini_key_input

    st.divider()

    # FILTER RISET
    st.subheader("🕵️ Mode & Filter")
    research_mode = st.radio("Metode:", ["Trending Umum", "Pencarian Niche"])
    
    # Filter Waktu
    time_filter = st.selectbox(
        "Waktu Upload:",
        ["Semua Waktu", "Hari Ini (24 Jam)", "Minggu Ini (7 Hari)", "Bulan Ini (30 Hari)"],
        index=0
    )
    
    # Filter Jumlah
    max_results_filter = st.slider("Jumlah Data:", 10, 50, 10, 5)

    target_region = st.selectbox("Wilayah:", ["ID", "US", "KR", "JP"], index=0)
    
    search_query = ""
    if research_mode == "Pencarian Niche":
        search_query = st.text_input("Kata Kunci:", placeholder="Contoh: Tutorial Masak")

# --- MAIN DASHBOARD ---
st.title("📹 YouTube Trend Intelligence Pro")

if st.session_state.get('is_valid'):
    
    # LOGIKA WAKTU
    published_after_param = None
    filter_days = 0
    if time_filter == "Hari Ini (24 Jam)":
        filter_days = 1
        published_after_param = (datetime.utcnow() - timedelta(days=1)).isoformat("T") + "Z"
    elif time_filter == "Minggu Ini (7 Hari)":
        filter_days = 7
        published_after_param = (datetime.utcnow() - timedelta(days=7)).isoformat("T") + "Z"
    elif time_filter == "Bulan Ini (30 Hari)":
        filter_days = 30
        published_after_param = (datetime.utcnow() - timedelta(days=30)).isoformat("T") + "Z"

    # EKSEKUSI
    if research_mode == "Trending Umum":
        st.info(f"Analisis **Top {max_results_filter} Trending** wilayah **{target_region}** ({time_filter}).")
        if st.button("🚀 Mulai Analisa"):
            with st.spinner('Menarik data...'):
                df_result = get_trending_videos(st.session_state.api_key, region_code=target_region, max_results=max_results_filter)
                
                if not df_result.empty:
                    # Filter Waktu Manual untuk Trending
                    if filter_days > 0:
                        cutoff_date = (datetime.now() - timedelta(days=filter_days)).date()
                        df_result['Publish Date Obj'] = pd.to_datetime(df_result['Publish Date']).dt.date
                        df_result = df_result[df_result['Publish Date Obj'] >= cutoff_date]
                        df_result = df_result.drop(columns=['Publish Date Obj'])
                    
                    if not df_result.empty:
                        # Limit ulang sesuai slider
                        st.session_state.df_result = df_result.head(max_results_filter)
                        st.success(f"Dapat {len(st.session_state.df_result)} video!")
                    else:
                        st.warning(f"Tidak ada trending baru dalam {time_filter}.")
                else:
                    st.warning("Gagal ambil data.")

    elif research_mode == "Pencarian Niche":
        if search_query:
            st.info(f"Riset topik: **{search_query}** ({time_filter}).")
            if st.button("🚀 Mulai Deep Dive"):
                with st.spinner('Menarik data niche...'):
                    df_result = search_videos_niche(
                        st.session_state.api_key, 
                        query=search_query, 
                        region_code=target_region,
                        max_results=max_results_filter,
                        published_after=published_after_param
                    )
                    if not df_result.empty:
                        st.session_state.df_result = df_result
                        st.success(f"Dapat {len(df_result)} video!")
                    else:
                        st.warning("Tidak ada video ditemukan.")
        else:
            st.warning("Isi kata kunci dulu.")

    # VISUALISASI KARTU
    if 'df_result' in st.session_state and not st.session_state.df_result.empty:
        df = st.session_state.df_result
        
        st.divider()
        st.subheader(f"🔥 Hasil Analisis ({len(df)} Video)")
        
        top_videos = df.sort_values(by="Views", ascending=False)
        
        for index, row in top_videos.iterrows():
            with st.container():
                col1, col2, col3 = st.columns([1, 2, 1])
                
                with col1:
                    st.image(row['Thumbnail'], use_container_width=True)
                
                with col2:
                    st.markdown(f"#### [{row['Title']}]({row['URL']})")
                    st.markdown(f"**Channel:** {row['Channel']}")
                    
                    views, subs = row['Views'], row['Subscribers']
                    if subs > 0:
                        ratio = views / subs
                        if ratio > 5: st.success(f"🚀 **VIRAL:** Views {round(ratio,1)}x Subs!")
                        elif ratio > 1: st.info("📈 **GOOD:** Views > Subs")
                    
                    st.caption(f"📅 {row['Publish Date']} | ⏱️ {row['Duration (Min)']} Min")

                    with st.expander("🔍 Detail (Tags & ID)"):
                        tags = row['Tags']
                        if tags: st.code(", ".join(tags), language="text")
                        else: st.caption("Tanpa tags.")
                        st.markdown(f"[Download Thumbnail]({row['Thumbnail']})")
                        st.text_input("ID:", row['Video ID'], key=f"v_{index}")

                with col3:
                    st.metric("Views", f"{views:,}")
                    st.metric("Subs", f"{subs:,}")
                    eng = row['Engagement (%)']
                    st.metric("Engagement", f"{eng}%", delta="High" if eng > 5 else None)

            st.divider()

        # AI STRATEGIST
        st.header("🤖 AI Content Strategist")
        if st.session_state.get('gemini_key'):
            if st.button("✨ Minta Saran AI", type="primary"):
                with st.spinner("AI sedang berpikir..."):
                    topic = search_query if research_mode == "Pencarian Niche" else f"Trending {target_region}"
                    result = generate_ai_strategy(st.session_state.gemini_key, df.to_dict('records'), topic)
                    st.success("Selesai!")
                    st.markdown(result)
        else:
            st.info("Masukkan Gemini Key untuk fitur ini.")

else:
    st.markdown("### 👋 Masukkan YouTube API Key di Sidebar.")
