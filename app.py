import streamlit as st
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import google.generativeai as genai
import pandas as pd
import isodate
from datetime import datetime, timedelta

# --- KONFIGURASI ---
st.set_page_config(page_title="YouTube Trend Pro +", page_icon="📹", layout="wide", initial_sidebar_state="expanded")

# --- FUNGSI HELPER & DATA ---
def parse_duration(duration_str):
    try:
        if not duration_str: return 0
        return isodate.parse_duration(duration_str).total_seconds()
    except: return 0

def get_channel_stats(youtube, channel_ids):
    try:
        unique_ids = list(set(channel_ids))
        stats = {}
        for i in range(0, len(unique_ids), 50):
            chunk = unique_ids[i:i+50]
            if not chunk: continue
            req = youtube.channels().list(part="statistics", id=','.join(chunk))
            res = req.execute()
            for item in res.get('items', []):
                stats[item['id']] = int(item['statistics'].get('subscriberCount', 0))
        return stats
    except: return {}

def process_video_items(items, youtube_service=None):
    data = []
    if not items: return pd.DataFrame()
    
    # 1. Channel IDs
    channel_ids = [item['snippet'].get('channelId') for item in items if 'snippet' in item]
    
    # 2. Subscribers
    channel_stats = {}
    if youtube_service and channel_ids:
        channel_stats = get_channel_stats(youtube_service, channel_ids)
    
    # 3. Process
    for item in items:
        vid_id = item['id'] if isinstance(item['id'], str) else item['id'].get('videoId')
        if not vid_id: continue
            
        snip = item.get('snippet', {})
        stat = item.get('statistics', {})
        content = item.get('contentDetails', {})
        
        view = int(stat.get('viewCount', 0))
        like = int(stat.get('likeCount', 0))
        comment = int(stat.get('commentCount', 0))
        eng = ((like + comment) / view * 100) if view > 0 else 0
        dur_sec = parse_duration(content.get('duration', 'PT0S'))
        
        data.append({
            'Video ID': vid_id,
            'Title': snip.get('title', ''),
            'Channel': snip.get('channelTitle', ''),
            'Subscribers': channel_stats.get(snip.get('channelId'), 0),
            'Views': view,
            'Engagement (%)': round(eng, 2),
            'Duration (Min)': round(dur_sec / 60, 2),
            'Publish Date': snip.get('publishedAt', '').split('T')[0],
            'Thumbnail': snip.get('thumbnails', {}).get('high', {}).get('url', ''),
            'Tags': snip.get('tags', []),
            'URL': f"https://www.youtube.com/watch?v={vid_id}"
        })
    return pd.DataFrame(data)

# --- AI ---
def generate_ai_strategy(api_key, video_data, topic):
    try:
        genai.configure(api_key=api_key)
        # Auto Model
        models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        sel_model = next((m for m in ['models/gemini-1.5-flash', 'models/gemini-pro'] if m in models), models[0] if models else "")
        
        if not sel_model: return "❌ No AI Model found."
        
        model = genai.GenerativeModel(sel_model)
        data_text = "\n".join([f"{i+1}. {v['Title']} ({v['Views']} views)" for i, v in enumerate(video_data[:5])])
        prompt = f"Analisa top 5 video '{topic}':\n{data_text}\n\nBerikan 3 ide judul viral, analisa kenapa laku, dan saran thumbnail. Bahasa Indonesia."
        return f"**AI Model: {sel_model}**\n\n" + model.generate_content(prompt).text
    except Exception as e: return f"Error AI: {str(e)}"

# --- API CALLS ---
def get_yt_service(key):
    try: return build('youtube', 'v3', developerKey=key)
    except: return None

@st.cache_data(show_spinner=False)
def get_data(api_key, mode, query, region, max_res, pub_after):
    yt = get_yt_service(api_key)
    try:
        if mode == "Trending Umum":
            req = yt.videos().list(part="snippet,contentDetails,statistics", chart="mostPopular", regionCode=region, maxResults=max_res)
            return process_video_items(req.execute().get('items', []), yt)
        else:
            params = {"part": "id", "q": query, "type": "video", "regionCode": region, "maxResults": max_res}
            if pub_after: params["publishedAfter"] = pub_after
            res = yt.search().list(**params).execute()
            ids = [i['id']['videoId'] for i in res.get('items', [])]
            if not ids: return pd.DataFrame()
            req_v = yt.videos().list(part="snippet,contentDetails,statistics", id=','.join(ids))
            return process_video_items(req_v.execute().get('items', []), yt)
    except Exception as e:
        st.error(f"Error: {e}")
        return pd.DataFrame()

# --- SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Konfigurasi")
    if 'api_key' not in st.session_state: st.session_state.api_key = ''
    st.session_state.api_key = st.text_input("YouTube Key", type="password", value=st.session_state.api_key)
    
    if 'gemini_key' not in st.session_state: st.session_state.gemini_key = ''
    st.session_state.gemini_key = st.text_input("Gemini Key (Opsional)", type="password", value=st.session_state.gemini_key)
    
    st.divider()
    st.subheader("Filter Riset")
    mode = st.radio("Mode:", ["Trending Umum", "Pencarian Niche"])
    time_opt = st.selectbox("Waktu:", ["Semua", "Hari Ini", "Minggu Ini", "Bulan Ini"])
    max_res = st.slider("Jml Data:", 10, 50, 10, 5)
    
    st.markdown("---")
    # FILTER BARU
    sort_opt = st.selectbox("Urutkan:", ["Views Terbanyak", "Paling Baru", "Engagement Tinggi", "Subs Terbanyak"])
    dur_opt = st.radio("Durasi:", ["Semua", "Shorts (<1m)", "Pendek (1-5m)", "Sedang (5-20m)", "Panjang (>20m)"])
    
    region = st.selectbox("Wilayah:", ["ID", "US", "KR", "JP"])
    query = st.text_input("Keyword:") if mode == "Pencarian Niche" else ""

# --- MAIN ---
st.title("📹 YouTube Trend Pro +")

if st.session_state.api_key:
    # Logic Waktu
    pub_after = None
    days = {"Hari Ini": 1, "Minggu Ini": 7, "Bulan Ini": 30}.get(time_opt, 0)
    if days: pub_after = (datetime.utcnow() - timedelta(days=days)).isoformat("T") + "Z"

    if st.button("🚀 Mulai Riset"):
        if mode == "Pencarian Niche" and not query: st.warning("Isi keyword!")
        else:
            with st.spinner("Mengambil data..."):
                df = get_data(st.session_state.api_key, mode, query, region, max_res, pub_after)
                if not df.empty:
                    # Filter Manual Trending Waktu
                    if mode == "Trending Umum" and days:
                        cut = (datetime.now() - timedelta(days=days)).date()
                        df = df[pd.to_datetime(df['Publish Date']).dt.date >= cut]
                    st.session_state.df_result = df
                    if df.empty: st.warning("Data kosong setelah filter waktu.")
                else: st.warning("Data tidak ditemukan.")

    # VISUALISASI DENGAN FILTER BARU
    if 'df_result' in st.session_state and not st.session_state.df_result.empty:
        df_show = st.session_state.df_result.copy()
        
        # Filter Durasi
        if dur_opt == "Shorts (<1m)": df_show = df_show[df_show['Duration (Min)'] < 1]
        elif dur_opt == "Pendek (1-5m)": df_show = df_show[(df_show['Duration (Min)'] >= 1) & (df_show['Duration (Min)'] <= 5)]
        elif dur_opt == "Sedang (5-20m)": df_show = df_show[(df_show['Duration (Min)'] > 5) & (df_show['Duration (Min)'] <= 20)]
        elif dur_opt == "Panjang (>20m)": df_show = df_show[df_show['Duration (Min)'] > 20]
        
        # Sorting
        if sort_opt == "Views Terbanyak": df_show = df_show.sort_values("Views", ascending=False)
        elif sort_opt == "Paling Baru": df_show = df_show.sort_values("Publish Date", ascending=False)
        elif sort_opt == "Engagement Tinggi": df_show = df_show.sort_values("Engagement (%)", ascending=False)
        elif sort_opt == "Subs Terbanyak": df_show = df_show.sort_values("Subscribers", ascending=False)
        
        st.divider()
        if len(df_show) == 0:
            st.warning(f"Tidak ada video durasi '{dur_opt}' dari hasil pencarian.")
        else:
            st.subheader(f"🔥 Hasil: {len(df_show)} Video")
                        # ... (bagian loop visualisasi) ...
            
                        # LOOP VISUALISASI (VERSI HIGH VISIBILITY)
            for i, row in df_show.iterrows():
                
                # Logic Viral: Hitung Rasio Views vs Subs
                is_viral = False
                viral_ratio = 0
                if row['Subscribers'] > 0:
                    viral_ratio = row['Views'] / row['Subscribers']
                    if viral_ratio >= 5.0: # Ambang batas viral (5x lipat subs)
                        is_viral = True

                # Container Kartu
                with st.container(border=True): # Tambah border biar rapi
                    
                    # Jika Viral, beri tanda spesial di paling atas
                    if is_viral:
                        st.warning(f"🔥 **SUPER VIRAL!** (Views {round(viral_ratio, 1)}x lebih banyak dari Subscriber)")
                    
                    c1, c2, c3 = st.columns([1, 2, 1])
                    
                    # Kolom 1: Gambar
                    with c1: 
                        st.image(row['Thumbnail'], use_container_width=True)
                    
                    # Kolom 2: Detail
                    with c2:
                        # Judul
                        st.markdown(f"#### [{row['Title']}]({row['URL']})")
                        
                        # Info Meta
                        st.caption(f"📺 {row['Channel']} | 📅 {row['Publish Date']} | ⏱️ {row['Duration (Min)']} Menit")
                        
                        # Expander Detail
                        with st.expander("🔍 Intip Resep (Tags & ID)"):
                            tags = row['Tags']
                            if isinstance(tags, list) and len(tags) > 0:
                                st.markdown("**Tags:**")
                                st.code(", ".join(tags), language='text')
                            else:
                                st.caption("Tanpa tags.")
                            
                            st.markdown(f"🖼️ [Download Thumbnail]({row['Thumbnail']})")
                            st.text_input("Copy ID", row['Video ID'], key=f"vid_{row['Video ID']}") # Key aman

                    # Kolom 3: Metrik
                    with c3:
                        # Warna angka Views jadi hijau jika viral
                        st.metric("Views", f"{row['Views']:,}", delta="Viral" if is_viral else None)
                        st.metric("Eng. Rate", f"{row['Engagement (%)']}%")
                        st.metric("Subs", f"{row['Subscribers']:,}")
                        
                        # Tombol CTA (Call to Action)
                        if is_viral:
                             st.caption("✅ **Rekomendasi ATM**")
                
                # Jarak antar kartu
                st.write("") 
            
            # AI
            st.header("🤖 AI Consultant")
            if st.session_state.gemini_key and st.button("✨ Analisa AI"):
                with st.spinner("AI analyzing..."):
                    res = generate_ai_strategy(st.session_state.gemini_key, df_show.to_dict('records'), query or f"Trending {region}")
                    st.markdown(res)
else:
    st.info("Masukkan YouTube API Key di sidebar.")
