import io
import html
import numpy as np
import pandas as pd
import folium
from folium import plugins
import streamlit as st
from streamlit_folium import st_folium

st.set_page_config(page_title="Ops Control Center - Kalimantan", layout="wide", page_icon="📡")

# ==========================================
# --- 1. KONFIGURASI NAMA KOLOM KOORDINAT ---
# ==========================================
TIM_LAT, TIM_LON = "Lat", "Long"
SITE_LAT, SITE_LON = "Latitude Tujuan", "Longtitude Tujuan"
HD_LAT, HD_LON = "Lat", "Long"

ALIASES = {
    TIM_LAT: ["lat", "latitude"],
    TIM_LON: ["long", "lon", "longitude", "longtitude"],
    SITE_LAT: ["latitude tujuan", "latitude", "lat"],
    SITE_LON: ["longtitude tujuan", "longitude tujuan", "longitude", "longtitude", "long", "lon"],
}

# ==========================================
# --- 2. FUNGSI HELPER & ENGINE PEMETAAN ---
# ==========================================
def to_num(series):
    if pd.api.types.is_numeric_dtype(series):
        return series.astype(float)
    cleaned = series.astype(str).str.strip().str.replace(",", ".", regex=False)
    return pd.to_numeric(cleaned, errors="coerce")

def valid_coord(lat, lon):
    return lat.notna() & lon.notna() & lat.between(-90, 90) & lon.between(-180, 180)

def clean_df(df, lat_col, lon_col):
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated()]
    df = df.drop(columns=[c for c in df.columns if c.startswith("Unnamed") and df[c].isna().all()])

    lower_map = {c.lower(): c for c in df.columns}
    for target in (lat_col, lon_col):
        if target not in df.columns:
            for alias in ALIASES.get(target, [target.lower()]):
                if alias in lower_map and lower_map[alias] not in (lat_col, lon_col):
                    df = df.rename(columns={lower_map[alias]: target})
                    break

    for c in (lat_col, lon_col):
        if c in df.columns:
            df[c] = to_num(df[c])

    for c in df.columns:
        if df[c].dtype == object and pd.api.types.infer_dtype(df[c], skipna=True) in ("mixed", "mixed-integer"):
            df[c] = df[c].where(df[c].isna(), df[c].astype(str))
    return df

def haversine_matrix(lat1, lon1, lat2, lon2):
    lat1, lon1 = np.radians(lat1)[:, None], np.radians(lon1)[:, None]
    lat2, lon2 = np.radians(lat2)[None, :], np.radians(lon2)[None, :]
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))

def col_values(df, name, default):
    if name in df.columns:
        return df[name].fillna(default).to_numpy()
    return np.array([default] * len(df), dtype=object)

def build_coverage(df_tim, df_site, chunk=2000):
    t_lat, t_lon = to_num(df_tim[TIM_LAT]), to_num(df_tim[TIM_LON])
    s_lat, s_lon = to_num(df_site[SITE_LAT]), to_num(df_site[SITE_LON])
    team_ok = valid_coord(t_lat, t_lon)
    site_ok = valid_coord(s_lat, s_lon)

    teams, sites = df_tim[team_ok], df_site[site_ok]
    if teams.empty or sites.empty:
        return pd.DataFrame()

    tlat, tlon = t_lat[team_ok].to_numpy(), t_lon[team_ok].to_numpy()
    slat, slon = s_lat[site_ok].to_numpy(), s_lon[site_ok].to_numpy()

    nearest, min_dist = np.empty(len(sites), dtype=int), np.empty(len(sites))
    for start in range(0, len(sites), chunk):
        sl = slice(start, start + chunk)
        d = haversine_matrix(slat[sl], slon[sl], tlat, tlon)
        idx = d.argmin(axis=1)
        nearest[sl] = idx
        min_dist[sl] = d[np.arange(len(idx)), idx]

    best = teams.iloc[nearest]
    return pd.DataFrame({
        "Site ID": col_values(sites, "Site ID", "Unknown Site"),
        "Site Lat": slat, "Site Long": slon,
        "Nearest Team": col_values(best, "NAMA", "Unknown"), 
        "Team Lat": tlat[nearest], "Team Long": tlon[nearest],
        "Attitude": col_values(best, "ATTITUDE TIM", "UNKNOWN"),
        "Jarak (KM)": np.round(min_dist, 2),
    })

def attitude_color(att):
    att = str(att).upper()
    return "green" if "GOOD" in att else "orange" if "MID" in att else "red"

def esc(value, default="-"):
    return default if pd.isna(value) else html.escape(str(value))

def convert_df_to_excel(df, sheet_name="Data"):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    return output.getvalue()

# ==========================================
# --- 3. INISIALISASI SESSION STATE ---
# ==========================================
def init_state(key, default_cols):
    if key not in st.session_state:
        st.session_state[key] = pd.DataFrame(columns=default_cols)

init_state('df_relokasi', ['Site Donor', 'Site Acceptor', 'Tipe Battery', 'Status', 'Tanggal'])
init_state('df_tim', ['NO', 'Regional', 'Cluster', 'NAMA', 'Lat', 'Long', 'STATUS', 'ROLE', 'ATTITUDE TIM', 'REMARK'])
init_state('df_site', ['Site ID', 'Latitude Tujuan', 'Longtitude Tujuan'])
init_state('df_helpdesk', ['Nama Helpdesk', 'Area', 'Lat', 'Long', 'Kapasitas Handling'])
init_state('df_sparepart', ['Kode Part', 'Nama Part', 'Kategori', 'Stok Fisik', 'Lokasi Simpan'])

# ==========================================
# --- 4. MENU SIDEBAR (KONSEP 5 POIN) ---
# ==========================================
st.sidebar.image("https://upload.wikimedia.org/wikipedia/commons/thumb/c/c2/Telkom_Indonesia_2013.svg/1200px-Telkom_Indonesia_2013.svg.png", width=150)
st.sidebar.title("Navigasi Operasional")
menu = st.sidebar.radio("Pilih Modul:", [
    "1. 🔋 Relokasi Battery",
    "2. 👥 Monitoring Lamandau & Tim",
    "3. 🗺️ Team Re-Mapping & Coverage",
    "4. 🎧 Helpdesk Mapping",
    "5. ⚙️ Spare Part Management"
])

st.sidebar.markdown("---")
st.sidebar.caption("Sistem Control Data Regional Kalimantan.")

if "flash" in st.session_state:
    st.success(st.session_state.pop("flash"))

# ==========================================
# --- 5. KONTEN BERDASARKAN MENU ---
# ==========================================

# ----------------------------------------
# MENU 1: RELOKASI BATTERY
# ----------------------------------------
if menu == "1. 🔋 Relokasi Battery":
    st.header("Relokasi Battery (Bronze to Gold & Platinum)")
    
    if st.session_state.df_relokasi.empty:
        st.info("Tabel Relokasi masih kosong. Anda bisa meng-upload file Excel (contoh: relokasi.xlsx) atau langsung mengisinya di bawah.")
        f_upload = st.file_uploader("Upload Data Relokasi (Excel)", type=['xlsx'])
        if f_upload:
            st.session_state.df_relokasi = pd.read_excel(f_upload)
            st.rerun()
            
    edited_df = st.data_editor(st.session_state.df_relokasi, num_rows="dynamic", use_container_width=True, height=400)
    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("💾 Update Relokasi", use_container_width=True):
            st.session_state.df_relokasi = edited_df
            st.session_state.flash = "✅ Data Relokasi Berhasil Disimpan!"
            st.rerun()
    with col2:
        st.download_button("📥 Download Master Relokasi", convert_df_to_excel(st.session_state.df_relokasi, "Relokasi"), "Master_Relokasi.xlsx")

# ----------------------------------------
# MENU 2: MONITORING TIM
# ----------------------------------------
elif menu == "2. 👥 Monitoring Lamandau & Tim":
    st.header("Monitoring Tim & Spesial Cluster")
    
    if st.session_state.df_tim.empty:
        st.warning("⚠️ Data Tim kosong! Silakan upload file 'tim_monitoring.xlsx' Anda di sini:")
        f_upload = st.file_uploader("Upload tim_monitoring.xlsx", type=['xlsx'])
        if f_upload:
            st.session_state.df_tim = pd.read_excel(f_upload)
            st.rerun()

    if not st.session_state.df_tim.empty:
        edited_tim = st.data_editor(st.session_state.df_tim, num_rows="dynamic", use_container_width=True, height=400)
        col1, col2 = st.columns([1, 4])
        with col1:
            if st.button("💾 Update Tim", use_container_width=True):
                st.session_state.df_tim = clean_df(edited_tim, TIM_LAT, TIM_LON)
                st.session_state.flash = "✅ Data Tim Berhasil Disimpan!"
                st.rerun()
        with col2:
            st.download_button("📥 Download Master Tim", convert_df_to_excel(st.session_state.df_tim, "Tim"), "Master_Tim.xlsx")

# ----------------------------------------
# MENU 3: TEAM RE-MAPPING & COVERAGE
# ----------------------------------------
elif menu == "3. 🗺️ Team Re-Mapping & Coverage":
    st.header("Team Enhancement & Re-Mapping")
    
    # Peringatan jika data belum di-upload
    if st.session_state.df_tim.empty or st.session_state.df_site.empty:
        st.error("🚨 Anda belum meng-upload Master Data Tim atau Site. Peta tidak bisa digambar.")
        st.info("Upload file Excel Anda di bawah ini:")
        col_up1, col_up2 = st.columns(2)
        with col_up1:
            f_tim = st.file_uploader("1. Upload tim_monitoring.xlsx", type=['xlsx'])
            if f_tim:
                st.session_state.df_tim = pd.read_excel(f_tim)
                st.rerun()
        with col_up2:
            f_site = st.file_uploader("2. Upload site_master.xlsx", type=['xlsx'])
            if f_site:
                st.session_state.df_site = pd.read_excel(f_site)
                st.rerun()

    # Jika data sudah lengkap, tampilkan Peta & Tabel
    if not st.session_state.df_tim.empty and not st.session_state.df_site.empty:
        tab_map, tab_site = st.tabs(["🗺️ Peta & Analisis Beban Kerja", "🗼 Update Master Site"])
        
        with tab_map:
            df_coverage = build_coverage(st.session_state.df_tim, st.session_state.df_site)
            
            if df_coverage.empty:
                st.warning("Pastikan format kolom koordinat Tim dan Site (Lat/Long) sudah terisi.")
            else:
                m = folium.Map(location=[df_coverage["Site Lat"].mean(), df_coverage["Site Long"].mean()], zoom_start=8, tiles="CartoDB positron")
                plugins.Fullscreen().add_to(m)

                team_ok = valid_coord(to_num(st.session_state.df_tim[TIM_LAT]), to_num(st.session_state.df_tim[TIM_LON]))
                for _, team in st.session_state.df_tim[team_ok].iterrows():
                    warna = attitude_color(team.get("ATTITUDE TIM", ""))
                    html_popup = f"<div style='width: 200px;'><h4 style='color: {warna};'>{esc(team.get('NAMA'))}</h4><b>Attitude:</b> {esc(team.get('ATTITUDE TIM'))}</div>"
                    folium.Marker([float(team[TIM_LAT]), float(team[TIM_LON])], popup=folium.Popup(html_popup, max_width=250), icon=folium.Icon(color=warna, icon="wrench", prefix="fa")).add_to(m)

                for row in df_coverage.itertuples(index=False):
                    folium.CircleMarker(location=[row[1], row[2]], radius=4, color="black", fill=True, popup=f"<b>{esc(row[0])}</b><br>Tim: {esc(row[3])}<br>Jarak: {row[7]} KM").add_to(m)
                    plugins.AntPath(locations=[[row[4], row[5]], [row[1], row[2]]], color="blue", weight=2, opacity=0.5, dash_array=[10, 20], delay=1000, tooltip=f"{esc(row[0])} ➔ {esc(row[3])} ({row[7]} KM)").add_to(m)

                st_folium(m, width=1300, height=500, returned_objects=[])
                
                st.markdown("---")
                st.subheader("📊 Insight: Distribusi Beban Coverage Tim & Jarak")
                
                col_insight1, col_insight2 = st.columns([1, 2])
                with col_insight1:
                    st.markdown("**1. Tim dengan Beban Site Terbanyak (Workload)**")
                    df_workload = df_coverage.groupby('Nearest Team')['Site ID'].count().reset_index()
                    df_workload.columns = ['Nama Tim', 'Total Site (Coverage)']
                    df_workload = df_workload.sort_values('Total Site (Coverage)', ascending=False).reset_index(drop=True)
                    st.dataframe(df_workload, use_container_width=True, height=350)
                    
                with col_insight2:
                    st.markdown("**2. Detail Jarak Site ke Tim Terdekat**")
                    df_detail = df_coverage[['Site ID', 'Nearest Team', 'Attitude', 'Jarak (KM)']]
                    st.dataframe(df_detail, use_container_width=True, height=350)
                
                st.download_button("📥 Download Laporan Mapping Coverage Lengkap", convert_df_to_excel(df_coverage, "Mapping_Coverage"), "Analisis_Mapping_Coverage.xlsx", type="primary", use_container_width=True)

        with tab_site:
            edited_site = st.data_editor(st.session_state.df_site, num_rows="dynamic", use_container_width=True, height=400)
            col1, col2 = st.columns([1, 4])
            with col1:
                if st.button("💾 Update Site", use_container_width=True):
                    st.session_state.df_site = clean_df(edited_site, SITE_LAT, SITE_LON)
                    st.session_state.flash = "✅ Data Site Berhasil Disimpan!"
                    st.rerun()
            with col2:
                st.download_button("📥 Master Site (Excel)", convert_df_to_excel(st.session_state.df_site, "Site"), "Master_Site.xlsx")

# ----------------------------------------
# MENU 4: HELPDESK MAPPING
# ----------------------------------------
elif menu == "4. 🎧 Helpdesk Mapping":
    st.header("Helpdesk Mapping - Pangkalanbun")
    
    if st.session_state.df_helpdesk.empty:
        st.info("Upload file Helpdesk.xlsx jika Anda punya datanya, atau ketik langsung di tabel bawah.")
        f_hd = st.file_uploader("Upload Data Helpdesk", type=['xlsx'])
        if f_hd:
            st.session_state.df_helpdesk = pd.read_excel(f_hd)
            st.rerun()

    df_hd = st.session_state.df_helpdesk
    if not df_hd.empty and HD_LAT in df_hd.columns:
        hd_ok = valid_coord(to_num(df_hd[HD_LAT]), to_num(df_hd[HD_LON]))
        if hd_ok.any():
            m_hd = folium.Map(location=[to_num(df_hd.loc[hd_ok, HD_LAT]).mean(), to_num(df_hd.loc[hd_ok, HD_LON]).mean()], zoom_start=9)
            for _, row in df_hd[hd_ok].iterrows():
                folium.Marker([float(row[HD_LAT]), float(row[HD_LON])], popup=f"<b>{row['Nama Helpdesk']}</b>", icon=folium.Icon(color="purple", icon="headphones", prefix="fa")).add_to(m_hd)
            st_folium(m_hd, width=1300, height=400, returned_objects=[])
            
    edited_hd = st.data_editor(df_hd, num_rows="dynamic", use_container_width=True)
    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("💾 Update Helpdesk", use_container_width=True):
            st.session_state.df_helpdesk = clean_df(edited_hd, HD_LAT, HD_LON)
            st.session_state.flash = "✅ Data Helpdesk Disimpan!"
            st.rerun()
    with col2:
        st.download_button("📥 Master Helpdesk", convert_df_to_excel(st.session_state.df_helpdesk, "Helpdesk"), "Master_Helpdesk.xlsx")

# ----------------------------------------
# MENU 5: SPARE PART MANAGEMENT
# ----------------------------------------
elif menu == "5. ⚙️ Spare Part Management":
    st.header("Spare Part Management")
    
    if st.session_state.df_sparepart.empty:
        st.info("Upload file Sparepart.xlsx jika Anda punya datanya.")
        f_sp = st.file_uploader("Upload Data Spare Part", type=['xlsx'])
        if f_sp:
            st.session_state.df_sparepart = pd.read_excel(f_sp)
            st.rerun()
            
    edited_sp = st.data_editor(st.session_state.df_sparepart, num_rows="dynamic", use_container_width=True, height=400)
    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("💾 Update Sparepart", use_container_width=True):
            st.session_state.df_sparepart = edited_sp
            st.session_state.flash = "✅ Data Spare Part Disimpan!"
            st.rerun()
    with col2:
        st.download_button("📥 Master Spare Part", convert_df_to_excel(st.session_state.df_sparepart, "Sparepart"), "Master_Sparepart.xlsx")
