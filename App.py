import html
import io

import folium
import numpy as np
import pandas as pd
import streamlit as st
from folium import plugins
from streamlit_folium import st_folium

st.set_page_config(page_title="Ops Control Center - Kalimantan", layout="wide", page_icon="📡")

# --- INISIALISASI SESSION STATE ---
for _key in ("df_tim", "df_site"):
    if _key not in st.session_state:
        st.session_state[_key] = pd.DataFrame()

# --- NAMA KOLOM YANG DIPAKAI APLIKASI ---
TIM_LAT, TIM_LON = "Lat", "Long"
SITE_LAT, SITE_LON = "Latitude Tujuan", "Longtitude Tujuan"

# Nama alternatif (huruf kecil) kalau header di Excel sedikit berbeda
ALIASES = {
    TIM_LAT: ["lat", "latitude"],
    TIM_LON: ["long", "lon", "longitude", "longtitude"],
    SITE_LAT: ["latitude tujuan", "latitude", "lat"],
    SITE_LON: ["longtitude tujuan", "longitude tujuan", "longitude", "longtitude", "long", "lon"],
}


# --- FUNGSI HELPER ---
def to_num(series):
    """Ubah kolom ke angka. Aman untuk teks seperti '-2,2136' (koma desimal) atau spasi."""
    if pd.api.types.is_numeric_dtype(series):
        return series.astype(float)
    cleaned = series.astype(str).str.strip().str.replace(",", ".", regex=False)
    return pd.to_numeric(cleaned, errors="coerce")


def valid_coord(lat, lon):
    return lat.notna() & lon.notna() & lat.between(-90, 90) & lon.between(-180, 180)


def clean_df(df, lat_col, lon_col):
    """Rapikan DataFrame hasil upload/edit. Return (df, daftar_kolom_koordinat_yang_tidak_ada)."""
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated()]
    df = df.drop(columns=[c for c in df.columns if c.startswith("Unnamed") and df[c].isna().all()])

    # Samakan nama kolom koordinat bila ada variasi penulisan
    lower_map = {c.lower(): c for c in df.columns}
    for target in (lat_col, lon_col):
        if target not in df.columns:
            for alias in ALIASES[target]:
                if alias in lower_map and lower_map[alias] not in (lat_col, lon_col):
                    df = df.rename(columns={lower_map[alias]: target})
                    break

    missing = [c for c in (lat_col, lon_col) if c not in df.columns]
    for c in (lat_col, lon_col):
        if c in df.columns:
            df[c] = to_num(df[c])

    # Kolom bertipe campur (angka + teks) bikin st.data_editor error (Arrow) -> jadikan teks
    for c in df.columns:
        if df[c].dtype == object and pd.api.types.infer_dtype(df[c], skipna=True) in ("mixed", "mixed-integer"):
            df[c] = df[c].where(df[c].isna(), df[c].astype(str))

    return df, missing


def haversine_matrix(lat1, lon1, lat2, lon2):
    """Jarak (KM) semua pasangan: lat1/lon1 (n,) x lat2/lon2 (m,) -> matriks (n, m)."""
    lat1 = np.radians(lat1)[:, None]
    lon1 = np.radians(lon1)[:, None]
    lat2 = np.radians(lat2)[None, :]
    lon2 = np.radians(lon2)[None, :]
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def col_values(df, name, default):
    if name in df.columns:
        return df[name].fillna(default).to_numpy()
    return np.array([default] * len(df), dtype=object)


def build_coverage(df_tim, df_site, chunk=2000):
    """Cari tim terdekat untuk setiap site (vectorized, jauh lebih cepat dari iterrows)."""
    t_lat, t_lon = to_num(df_tim[TIM_LAT]), to_num(df_tim[TIM_LON])
    s_lat, s_lon = to_num(df_site[SITE_LAT]), to_num(df_site[SITE_LON])
    team_ok = valid_coord(t_lat, t_lon)
    site_ok = valid_coord(s_lat, s_lon)
    n_skipped = int((~site_ok).sum())

    teams, sites = df_tim[team_ok], df_site[site_ok]
    if teams.empty or sites.empty:
        return pd.DataFrame(), n_skipped

    tlat, tlon = t_lat[team_ok].to_numpy(), t_lon[team_ok].to_numpy()
    slat, slon = s_lat[site_ok].to_numpy(), s_lon[site_ok].to_numpy()

    nearest = np.empty(len(sites), dtype=int)
    min_dist = np.empty(len(sites))
    for start in range(0, len(sites), chunk):  # dipotong-potong agar hemat memori
        sl = slice(start, start + chunk)
        d = haversine_matrix(slat[sl], slon[sl], tlat, tlon)
        idx = d.argmin(axis=1)
        nearest[sl] = idx
        min_dist[sl] = d[np.arange(len(idx)), idx]

    best = teams.iloc[nearest]
    df_cov = pd.DataFrame({
        "Site ID": col_values(sites, "Site ID", "Unknown Site"),
        "Site Lat": slat,
        "Site Long": slon,
        "Nearest Team": col_values(best, "Nama", "Unknown"),
        "Team Lat": tlat[nearest],
        "Team Long": tlon[nearest],
        "Attitude": col_values(best, "ATTITUDE TIM", "UNKNOWN"),
        "Jarak (KM)": np.round(min_dist, 2),
    })
    return df_cov, n_skipped


def attitude_color(att):
    att = str(att).upper()
    if "GOOD" in att:
        return "green"
    if "MID" in att:  # cocok untuk "MIDLE" maupun "MIDDLE"
        return "orange"
    return "red"


def esc(value, default="-"):
    return default if pd.isna(value) else html.escape(str(value))


def convert_df_to_excel(df, sheet_name="Data"):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    return output.getvalue()


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# --- HEADER & UPLOAD AWAL ---
st.title("📡 Engineering Project Control & Mapping System")
st.markdown("Dashboard interaktif untuk monitoring pergerakan tim, analisis coverage area, dan manajemen Master Data wilayah operasional Kalimantan.")

if "flash" in st.session_state:
    st.success(st.session_state.pop("flash"))

if st.session_state.df_tim.empty or st.session_state.df_site.empty:
    st.info("💡 Sistem mendeteksi Master Data kosong. Silakan upload file inisial terlebih dahulu.")
    col1, col2 = st.columns(2)
    with col1:
        file_tim = st.file_uploader("Upload Master Data Tim (ROAD MAP FME)", type=["xlsx"])
    with col2:
        file_site = st.file_uploader("Upload Master Data Site (Sidata)", type=["xlsx"])

    if file_tim and file_site:
        try:
            raw_tim = pd.read_excel(file_tim)
            raw_site = pd.read_excel(file_site)
        except Exception as e:
            st.error(f"Gagal membaca file Excel: {e}")
        else:
            new_tim, miss_tim = clean_df(raw_tim, TIM_LAT, TIM_LON)
            new_site, miss_site = clean_df(raw_site, SITE_LAT, SITE_LON)

            if miss_tim:
                st.error(f"File Tim: kolom {miss_tim} tidak ditemukan. Kolom yang terbaca: {list(new_tim.columns)}")
            if miss_site:
                st.error(f"File Site: kolom {miss_site} tidak ditemukan. Kolom yang terbaca: {list(new_site.columns)}")

            if not miss_tim and not miss_site:
                st.session_state.df_tim = new_tim
                st.session_state.df_site = new_site
                st.session_state.flash = "✅ Data Master berhasil dimuat!"
                st.rerun()

# --- DASHBOARD UTAMA ---
if not st.session_state.df_tim.empty and not st.session_state.df_site.empty:

    with st.sidebar:
        if st.button("🔄 Reset data & upload ulang", use_container_width=True):
            st.session_state.df_tim = pd.DataFrame()
            st.session_state.df_site = pd.DataFrame()
            st.rerun()

    tab_map, tab_tim, tab_site = st.tabs([
        "🗺️ Live Coverage & Mapping",
        "👥 Update Master Data Tim",
        "🗼 Update Master Data Site",
    ])

    # ==========================================
    # TAB 1: LIVE MAP & COVERAGE ANALYSIS
    # ==========================================
    with tab_map:
        st.subheader("Analisis Coverage Site Terhadap Titik Homebase Tim")

        df_tim_current = st.session_state.df_tim
        df_site_current = st.session_state.df_site
        df_coverage, n_skipped = build_coverage(df_tim_current, df_site_current)

        if n_skipped:
            st.warning(f"⚠️ {n_skipped} site dilewati karena koordinat kosong / tidak valid.")

        if df_coverage.empty:
            st.warning("Peta belum bisa dibuat: belum ada tim dan site dengan koordinat yang valid.")
        else:
            show_lines = st.checkbox(
                "Tampilkan garis animasi tim ➔ site",
                value=len(df_coverage) <= 300,
                help="Matikan jika jumlah site sangat banyak agar peta tetap ringan.",
            )

            m = folium.Map(
                location=[df_coverage["Site Lat"].mean(), df_coverage["Site Long"].mean()],
                zoom_start=8,
                tiles="CartoDB positron",
            )
            plugins.Fullscreen(position="topright").add_to(m)

            # 1. Marker Tim
            team_ok = valid_coord(to_num(df_tim_current[TIM_LAT]), to_num(df_tim_current[TIM_LON]))
            for _, team in df_tim_current[team_ok].iterrows():
                warna = attitude_color(team.get("ATTITUDE TIM", ""))
                html_popup = f"""
                <div style='font-family: Arial; width: 200px;'>
                    <h4 style='color: {warna}; margin-bottom: 2px;'>{esc(team.get('Nama'), 'Unknown')}</h4>
                    <b>Role:</b> {esc(team.get('ROLE'))}<br>
                    <b>Status:</b> {esc(team.get('STATUS'))}<br>
                    <b>Attitude:</b> {esc(team.get('ATTITUDE TIM'))}
                </div>
                """
                folium.Marker(
                    [float(team[TIM_LAT]), float(team[TIM_LON])],
                    popup=folium.Popup(html_popup, max_width=250),
                    icon=folium.Icon(color=warna, icon="wrench", prefix="fa"),
                ).add_to(m)

            # 2. Marker Site (+ garis animasi)
            for row in df_coverage.itertuples(index=False):
                site_id = esc(row[0])
                team_name = esc(row[3])
                jarak = row[7]
                folium.CircleMarker(
                    location=[row[1], row[2]],
                    radius=6, color="black", fill=True, fill_color="black", fill_opacity=0.8,
                    popup=f"<b>{site_id}</b><br>Covered by: {team_name}<br>Dist: {jarak} KM",
                ).add_to(m)

                if show_lines:
                    plugins.AntPath(
                        locations=[[row[4], row[5]], [row[1], row[2]]],  # [lat, long] tim -> site
                        color="blue", weight=2.5, opacity=0.6, dash_array=[10, 20], delay=1000,
                        tooltip=f"{site_id} ➔ {team_name} ({jarak} KM)",
                    ).add_to(m)

            # returned_objects=[] -> geser/zoom peta tidak memicu rerun seluruh script
            st_folium(m, width=1300, height=600, returned_objects=[], key="peta_coverage")

            excel_cov = convert_df_to_excel(df_coverage, "Analisis_Coverage")
            st.download_button(
                "📥 Download Laporan Coverage Area", excel_cov, "Report_Coverage.xlsx",
                mime=XLSX_MIME, type="primary", key="dl_coverage",
            )

    # ==========================================
    # TAB 2: UPDATE MASTER DATA TIM
    # ==========================================
    with tab_tim:
        st.subheader("👥 Database Personel & Mapping Tim")
        st.markdown("Edit langsung pada tabel di bawah. Data yang diubah akan otomatis mengubah peta coverage.")

        edited_tim = st.data_editor(
            st.session_state.df_tim, num_rows="dynamic", use_container_width=True, height=500, key="editor_tim"
        )

        col1, col2 = st.columns([1, 4])
        with col1:
            if st.button("💾 Simpan Perubahan Tim", use_container_width=True):
                st.session_state.df_tim, _ = clean_df(edited_tim, TIM_LAT, TIM_LON)
                st.session_state.flash = "✅ Data Tim diperbarui! Peta telah menyesuaikan."
                st.rerun()
        with col2:
            excel_tim = convert_df_to_excel(st.session_state.df_tim, "Sheet1")
            st.download_button(
                "📥 Download Master Tim Baru (Excel)", excel_tim, "UPDATE_ROAD_MAP_FME.xlsx",
                mime=XLSX_MIME, key="dl_tim",
            )

    # ==========================================
    # TAB 3: UPDATE MASTER DATA SITE
    # ==========================================
    with tab_site:
        st.subheader("🗼 Database Site & Area Tujuan")
        st.markdown("Perbarui data site (Donor/Acceptor/Master). Tambahkan Latitude dan Longitude baru di sini.")

        edited_site = st.data_editor(
            st.session_state.df_site, num_rows="dynamic", use_container_width=True, height=500, key="editor_site"
        )

        col1, col2 = st.columns([1, 4])
        with col1:
            if st.button("💾 Simpan Perubahan Site", use_container_width=True):
                st.session_state.df_site, _ = clean_df(edited_site, SITE_LAT, SITE_LON)
                st.session_state.flash = "✅ Data Site diperbarui! Peta telah menyesuaikan."
                st.rerun()
        with col2:
            excel_site = convert_df_to_excel(st.session_state.df_site, "Sheet1")
            st.download_button(
                "📥 Download Master Site Baru (Excel)", excel_site, "UPDATE_Site_Sidata.xlsx",
                mime=XLSX_MIME, key="dl_site",
            )
