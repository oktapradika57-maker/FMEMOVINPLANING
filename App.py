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
        else:
            df[c] = np.nan 

    for c in df.columns:
        if df[c].dtype == object and pd.api.types.infer_dtype(df[c], skipna=True) in ("mixed", "mixed-integer"):
            df[c] = df[c].where(df[c].isna(), df[c].astype(str))
    return df

def parse_program_excel(f_upload):
    """Fungsi auto-parse cerdas untuk membaca file Program Nop Pbun yang berantakan"""
    xls = pd.ExcelFile(f_upload)
    all_programs = []
    for s in xls.sheet_names:
        try:
            df = pd.read_excel(xls, sheet_name=s)
            header_row = -1
            # Mencari baris yang mengandung tulisan Site Id
            for i, row in df.head(15).iterrows():
                if any(str(v).strip().lower() in ['site id', 'site_id'] for v in row.values):
                    header_row = i
                    break
            
            if header_row != -1:
                df.columns = df.iloc[header_row]
                df = df.iloc[header_row+1:].reset_index(drop=True)
                df.columns = [str(c).strip() for c in df.columns]
                
                col_lower = {c.lower(): c for c in df.columns}
                if 'site id' in col_lower:
                    df = df.rename(columns={col_lower['site id']: 'Site ID'})
                
                if 'Site ID' in df.columns:
                    df = df.dropna(subset=['Site ID'])
                    if not df.empty:
                        all_programs.append(df)
        except Exception:
            continue

    if all_programs:
        df_merged = pd.concat(all_programs, ignore_index=True)
        cols_to_keep = ['Site ID']
        for c in ['Catagory', 'Ditail', 'Status', 'Pic']:
            for actual_col in df_merged.columns:
                if str(actual_col).strip().lower() == c.lower() and actual_col not in cols_to_keep:
                    cols_to_keep.append(actual_col)
        return df_merged[cols_to_keep]
    else:
        return pd.DataFrame(columns=["Site ID", "Catagory", "Ditail", "Status", "Pic"])

def haversine_matrix(lat1, lon1, lat2, lon2):
    lat1, lon1 = np.radians(lat1)[:, None], np.radians(lon1)[:, None]
    lat2, lon2 = np.radians(lat2)[None, :], np.radians(lon2)[None, :]
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))

def col_values(df, name, default):
    if name in df.columns:
        return df[name].fillna(default).to_numpy()
    return np.array([default] * len(df), dtype=object)

@st.cache_data
def build_coverage(df_tim, df_site, df_program, chunk=2000):
    out_cols = ["Site ID", "Site Class", "Site Lat", "Site Long", "Nearest Team", "Team Lat", "Team Long", "Attitude", "Jarak (KM)", "Program Aktif"]
    
    t_lat, t_lon = to_num(df_tim[TIM_LAT]), to_num(df_tim[TIM_LON])
    s_lat, s_lon = to_num(df_site[SITE_LAT]), to_num(df_site[SITE_LON])
    team_ok = valid_coord(t_lat, t_lon)
    site_ok = valid_coord(s_lat, s_lon)

    teams, sites = df_tim[team_ok], df_site[site_ok]
    
    if teams.empty or sites.empty:
        return pd.DataFrame(columns=out_cols)

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
    df_result = pd.DataFrame({
        "Site ID": col_values(sites, "Site ID", "Unknown Site"),
        "Site Class": col_values(sites, "Site Class", "Belum Ada Data"),
        "Site Lat": slat, "Site Long": slon,
        "Nearest Team": col_values(best, "NAMA", "Unknown"), 
        "Team Lat": tlat[nearest], "Team Long": tlon[nearest],
        "Attitude": col_values(best, "ATTITUDE TIM", "UNKNOWN"),
        "Jarak (KM)": np.round(min_dist, 2),
    })

    # Menggabungkan Data Program ke dalam Peta dan Tabel
    if not df_program.empty and 'Site ID' in df_program.columns:
        prog_agg = df_program.groupby('Site ID').apply(
            lambda x: "<br>".join([f"• {str(row.get('Catagory',''))} <i>({str(row.get('Status',''))})</i>" for _, row in x.iterrows()])
        ).reset_index(name='Program Aktif')
        df_result = pd.merge(df_result, prog_agg, on='Site ID', how='left')
        df_result['Program Aktif'] = df_result['Program Aktif'].fillna('-')
    else:
        df_result['Program Aktif'] = '-'

    return df_result

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
init_state('df_site', ['Site ID', 'Site Class', 'Latitude Tujuan', 'Longtitude Tujuan']) 
init_state('df_helpdesk', ['Nama Helpdesk', 'Area', 'Lat', 'Long', 'Kapasitas Handling'])
init_state('df_sparepart', ['Kode Part', 'Nama Part', 'Kategori', 'Stok Fisik', 'Lokasi Simpan'])
init_state('df_program', ['Site ID', 'Catagory', 'Ditail', 'Status', 'Pic'])

# ==========================================
# --- 4. MENU SIDEBAR ---
# ==========================================
st.sidebar.image("https://upload.wikimedia.org/wikipedia/commons/thumb/c/c2/Telkom_Indonesia_2013.svg/1200px-Telkom_Indonesia_2013.svg.png", width=150)
st.sidebar.title("Navigasi Operasional")
menu = st.sidebar.radio("Pilih Modul:", [
    "1. 🔋 Relokasi Battery",
    "2. 👥 Monitoring Lamandau & Tim",
    "3. 🗺️ Team Re-Mapping & Coverage",
    "4. 🎧 Helpdesk Mapping",
    "5. ⚙️ Spare Part Management",
    "6. 📋 Program Nop Pbun"
])

st.sidebar.markdown("---")
st.sidebar.caption("Sistem Control Data Regional Kalimantan.")

if "flash" in st.session_state:
    st.success(st.session_state.pop("flash"))

# ==========================================
# --- 5. KONTEN BERDASARKAN MENU ---
# ==========================================

# ----------------------------------------
# MENU 1 & 2 (RELOKASI & TIM)
# ----------------------------------------
if menu == "1. 🔋 Relokasi Battery":
    st.header("Relokasi Battery (Bronze to Gold & Platinum)")
    if st.session_state.df_relokasi.empty:
        st.info("Upload file Excel Relokasi Anda di bawah ini:")
        f_upload = st.file_uploader("Upload Data Relokasi", type=['xlsx'], key="up_relo")
        if f_upload:
            if st.button("🚀 Proses Data", type="primary"):
                f_upload.seek(0)
                st.session_state.df_relokasi = pd.read_excel(f_upload)
                st.rerun()
    if not st.session_state.df_relokasi.empty:
        edited_df = st.data_editor(st.session_state.df_relokasi, num_rows="dynamic", use_container_width=True, height=400)
        col1, col2 = st.columns([1, 4])
        with col1:
            if st.button("💾 Simpan Master Relokasi", use_container_width=True):
                st.session_state.df_relokasi = edited_df
                st.session_state.flash = "✅ Data Relokasi Berhasil Disimpan!"
                st.rerun()
        with col2:
            st.download_button("📥 Download Master Relokasi", convert_df_to_excel(st.session_state.df_relokasi, "Relokasi"), "Master_Relokasi.xlsx")

elif menu == "2. 👥 Monitoring Lamandau & Tim":
    st.header("Monitoring Tim & Spesial Cluster")
    if st.session_state.df_tim.empty:
        st.warning("⚠️ Data Tim kosong! Silakan upload file 'tim_monitoring.xlsx' Anda:")
        f_upload = st.file_uploader("Upload tim_monitoring.xlsx", type=['xlsx'], key="up_tim")
        if f_upload:
            if st.button("🚀 Proses Data", type="primary"):
                f_upload.seek(0)
                st.session_state.df_tim = clean_df(pd.read_excel(f_upload), TIM_LAT, TIM_LON)
                st.rerun()
    if not st.session_state.df_tim.empty:
        edited_tim = st.data_editor(st.session_state.df_tim, num_rows="dynamic", use_container_width=True, height=400)
        col1, col2 = st.columns([1, 4])
        with col1:
            if st.button("💾 Simpan Master Tim", use_container_width=True):
                st.session_state.df_tim = clean_df(edited_tim, TIM_LAT, TIM_LON)
                st.session_state.flash = "✅ Data Tim Berhasil Disimpan!"
                build_coverage.clear()
                st.rerun()
        with col2:
            st.download_button("📥 Download Master Tim", convert_df_to_excel(st.session_state.df_tim, "Tim"), "Master_Tim.xlsx")

# ----------------------------------------
# MENU 3: TEAM RE-MAPPING & COVERAGE (DENGAN PROGRAM AKTIF)
# ----------------------------------------
elif menu == "3. 🗺️ Team Re-Mapping & Coverage":
    st.header("Team Enhancement & Re-Mapping")
    
    if st.session_state.df_tim.empty or st.session_state.df_site.empty:
        st.error("🚨 Anda belum meng-upload Master Data Tim atau Site.")
        col_up1, col_up2, col_up3 = st.columns(3)
        with col_up1:
            f_tim = st.file_uploader("1. Upload tim_monitoring.xlsx", type=['xlsx'], key="up_map_tim")
        with col_up2:
            f_site = st.file_uploader("2. Upload site_master.xlsx", type=['xlsx'], key="up_map_site")
        with col_up3:
            f_prog = st.file_uploader("3. Program Nop Pbun (Opsional)", type=['xlsx'], key="up_map_prog")
            
        if f_tim is not None and f_site is not None:
            if st.button("🚀 PROSES DATA & TAMPILKAN PETA", use_container_width=True, type="primary"):
                f_tim.seek(0)
                f_site.seek(0)
                st.session_state.df_tim = clean_df(pd.read_excel(f_tim), TIM_LAT, TIM_LON)
                st.session_state.df_site = clean_df(pd.read_excel(f_site), SITE_LAT, SITE_LON)
                
                if f_prog is not None:
                    f_prog.seek(0)
                    st.session_state.df_program = parse_program_excel(f_prog)
                    
                build_coverage.clear()
                st.session_state.flash = "✅ Data Berhasil Dimuat!"
                st.rerun()

    else:
        tab_map, tab_site = st.tabs(["🗺️ Peta Interaktif & Filter", "🗼 Update Master Site"])
        
        with tab_map:
            # Kalkulasi Matriks beserta Gabungan Data Program
            df_coverage_master = build_coverage(st.session_state.df_tim, st.session_state.df_site, st.session_state.df_program)
            
            with st.expander("🔍 Filter & Pencarian Spesifik (Site ID, Nama Tim, Role)", expanded=True):
                col_f1, col_f2, col_f3 = st.columns(3)
                
                roles = st.session_state.df_tim['ROLE'].dropna().unique().tolist() if 'ROLE' in st.session_state.df_tim.columns else []
                teams = st.session_state.df_tim['NAMA'].dropna().unique().tolist() if 'NAMA' in st.session_state.df_tim.columns else []
                sites = df_coverage_master['Site ID'].dropna().unique().tolist() if not df_coverage_master.empty else []

                with col_f1:
                    f_role = st.multiselect("Filter Berdasarkan Role Tim:", options=roles, placeholder="Semua Role (Kosongkan)")
                with col_f2:
                    f_nama = st.multiselect("Filter Berdasarkan Nama Tim:", options=teams, placeholder="Semua Tim (Kosongkan)")
                with col_f3:
                    f_site = st.multiselect("Pencarian Site ID:", options=sites, placeholder="Ketik Site ID...")

            df_cov_filtered = df_coverage_master.copy()
            df_tim_filtered = st.session_state.df_tim.copy()

            if f_role:
                df_tim_filtered = df_tim_filtered[df_tim_filtered['ROLE'].isin(f_role)]
                valid_teams = df_tim_filtered['NAMA'].tolist() if 'NAMA' in df_tim_filtered.columns else []
                df_cov_filtered = df_cov_filtered[df_cov_filtered['Nearest Team'].isin(valid_teams)]

            if f_nama:
                df_tim_filtered = df_tim_filtered[df_tim_filtered['NAMA'].isin(f_nama)]
                df_cov_filtered = df_cov_filtered[df_cov_filtered['Nearest Team'].isin(f_nama)]

            if f_site:
                df_cov_filtered = df_cov_filtered[df_cov_filtered['Site ID'].isin(f_site)]
                teams_for_sites = df_cov_filtered['Nearest Team'].unique()
                df_tim_filtered = df_tim_filtered[df_tim_filtered['NAMA'].isin(teams_for_sites)]

            if df_cov_filtered.empty and df_tim_filtered.empty:
                st.warning("Pencarian tidak menemukan hasil. Silakan ubah kriteria filter Anda.")
            else:
                lat_all = pd.concat([df_cov_filtered['Site Lat'], to_num(df_tim_filtered[TIM_LAT])]).dropna()
                lon_all = pd.concat([df_cov_filtered['Site Long'], to_num(df_tim_filtered[TIM_LON])]).dropna()
                
                if not lat_all.empty and not lon_all.empty:
                    m = folium.Map(location=[lat_all.mean(), lon_all.mean()], zoom_start=8)
                    m.fit_bounds([[lat_all.min(), lon_all.min()], [lat_all.max(), lon_all.max()]]) 
                else:
                    m = folium.Map(location=[-2.2, 113.9], zoom_start=6)

                plugins.Fullscreen().add_to(m)

                folium.TileLayer('CartoDB positron', name='Peta Terang Standar').add_to(m)
                folium.TileLayer(
                    tiles='https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}',
                    attr='Google Satellite', name='Peta Satelit (Google)',
                    overlay=False, control=True
                ).add_to(m)

                workload_dict = df_cov_filtered.groupby('Nearest Team').agg(
                    Total_Site=('Site ID', 'count'), Max_Jarak=('Jarak (KM)', 'max')
                ).reset_index().set_index('Nearest Team').to_dict('index')

                team_ok = valid_coord(to_num(df_tim_filtered[TIM_LAT]), to_num(df_tim_filtered[TIM_LON]))
                for _, team in df_tim_filtered[team_ok].iterrows():
                    team_name = team.get("NAMA", "Unknown")
                    warna = attitude_color(team.get("ATTITUDE TIM", ""))
                    t_lat = float(team[TIM_LAT])
                    t_lon = float(team[TIM_LON])
                    stats = workload_dict.get(team_name, {'Total_Site': 0, 'Max_Jarak': 0})

                    if stats['Total_Site'] > 0:
                        folium.Circle(
                            location=[t_lat, t_lon], radius=stats['Max_Jarak'] * 1000, 
                            color=warna, weight=1, fill=True, fill_opacity=0.10,
                            tooltip=f"🎈 Area Coverage {team_name} | {stats['Total_Site']} Site"
                        ).add_to(m)

                    html_popup = f"<div style='width: 200px;'><h4 style='color: {warna};'>{esc(team_name)}</h4><b>Role:</b> {esc(team.get('ROLE'))}<br><b>Attitude:</b> {esc(team.get('ATTITUDE TIM'))}<br><b>Site Di-cover:</b> {stats['Total_Site']}</div>"
                    folium.Marker([t_lat, t_lon], popup=folium.Popup(html_popup, max_width=250), icon=folium.Icon(color=warna, icon="user-gear", prefix="fa")).add_to(m)

                # Menyelipkan data program ke dalam Popup Marker Peta
                for row in df_cov_filtered.itertuples(index=False):
                    # Index 9 adalah Program Aktif
                    html_site = f"""
                    <div style='width: 280px; font-family: sans-serif; font-size: 13px;'>
                        <h5 style='color: #2c3e50; border-bottom: 1px solid #ccc; margin-bottom:5px;'>🗼 {esc(row[0])}</h5>
                        <b>Class:</b> {esc(row[1])}<br>
                        <b>Di-cover oleh:</b> {esc(row[4])}<br>
                        <b>Jarak Tempuh:</b> {row[8]} KM<br>
                        <hr style='margin: 8px 0;'>
                        <b style='color:#d35400;'>Program Aktif di Site ini:</b><br>
                        {row[9]}
                    </div>
                    """
                    folium.Marker(
                        location=[row[2], row[3]], popup=folium.Popup(html_site, max_width=320),
                        tooltip=f"Site: {esc(row[0])} ({esc(row[1])})", 
                        icon=folium.Icon(color="darkblue", icon="tower-broadcast", prefix="fa")
                    ).add_to(m)
                    
                    folium.PolyLine(
                        locations=[[row[5], row[6]], [row[2], row[3]]], 
                        color="#34495e", weight=1.5, opacity=0.6, dash_array="5, 5",
                        tooltip=f"Jarak ke {esc(row[4])}: {row[8]} KM"
                    ).add_to(m)

                folium.LayerControl().add_to(m)
                st_folium(m, width=1300, height=500, returned_objects=[])
                
                st.markdown("---")
                st.subheader(f"📊 Insight Analisis Site (Menampilkan {len(df_cov_filtered)} Site)")
                
                col_insight1, col_insight2 = st.columns([1, 2])
                with col_insight1:
                    st.markdown("**1. Distribusi Workload Tim**")
                    if not df_cov_filtered.empty:
                        df_workload = df_cov_filtered.groupby('Nearest Team')['Site ID'].count().reset_index()
                        df_workload.columns = ['Nama Tim', 'Total Site (Coverage)']
                        df_workload = df_workload.sort_values('Total Site (Coverage)', ascending=False).reset_index(drop=True)
                        st.dataframe(df_workload, use_container_width=True, height=350)
                    
                with col_insight2:
                    st.markdown("**2. Rekapan Penuh Site & Program Aktif**")
                    if not df_cov_filtered.empty:
                        # Menampilkan Status Program juga di dalam tabel
                        df_detail = df_cov_filtered[['Site ID', 'Site Class', 'Nearest Team', 'Attitude', 'Jarak (KM)', 'Program Aktif']]
                        st.dataframe(df_detail, use_container_width=True, height=350)
                
                if not df_cov_filtered.empty:
                    st.download_button("📥 Download Laporan Mapping Lengkap (Termasuk Program)", convert_df_to_excel(df_cov_filtered, "Mapping_Coverage"), "Analisis_Mapping_Coverage.xlsx", type="primary", use_container_width=True)

        with tab_site:
            st.info("💡 Tip: Anda dapat menambahkan kategori Bronze, Gold, atau Platinum pada kolom 'Site Class'.")
            edited_site = st.data_editor(st.session_state.df_site, num_rows="dynamic", use_container_width=True, height=400)
            col1, col2 = st.columns([1, 4])
            with col1:
                if st.button("💾 Simpan Master Site", use_container_width=True):
                    st.session_state.df_site = clean_df(edited_site, SITE_LAT, SITE_LON)
                    build_coverage.clear()
                    st.session_state.flash = "✅ Data Site Berhasil Disimpan!"
                    st.rerun()
            with col2:
                st.download_button("📥 Download Master Site", convert_df_to_excel(st.session_state.df_site, "Site"), "Master_Site.xlsx")

# ----------------------------------------
# MENU 4 & 5 (HELPDESK & SPARE PART)
# ----------------------------------------
elif menu == "4. 🎧 Helpdesk Mapping":
    st.header("Helpdesk Mapping - Pangkalanbun")
    if st.session_state.df_helpdesk.empty:
        f_hd = st.file_uploader("Upload Data Helpdesk", type=['xlsx'], key="up_hd")
        if f_hd:
            if st.button("🚀 Proses Data", type="primary"):
                f_hd.seek(0)
                st.session_state.df_helpdesk = clean_df(pd.read_excel(f_hd), HD_LAT, HD_LON)
                st.rerun()
    if not st.session_state.df_helpdesk.empty:
        df_hd = st.session_state.df_helpdesk
        if HD_LAT in df_hd.columns:
            hd_ok = valid_coord(to_num(df_hd[HD_LAT]), to_num(df_hd[HD_LON]))
            if hd_ok.any():
                m_hd = folium.Map(location=[to_num(df_hd.loc[hd_ok, HD_LAT]).mean(), to_num(df_hd.loc[hd_ok, HD_LON]).mean()], zoom_start=9)
                for _, row in df_hd[hd_ok].iterrows():
                    folium.Marker([float(row[HD_LAT]), float(row[HD_LON])], popup=f"<b>{row['Nama Helpdesk']}</b>", icon=folium.Icon(color="purple", icon="headphones", prefix="fa")).add_to(m_hd)
                st_folium(m_hd, width=1300, height=400, returned_objects=[])
        edited_hd = st.data_editor(df_hd, num_rows="dynamic", use_container_width=True)
        col1, col2 = st.columns([1, 4])
        with col1:
            if st.button("💾 Simpan Master Helpdesk", use_container_width=True):
                st.session_state.df_helpdesk = clean_df(edited_hd, HD_LAT, HD_LON)
                st.session_state.flash = "✅ Data Helpdesk Disimpan!"
                st.rerun()
        with col2:
            st.download_button("📥 Download Master Helpdesk", convert_df_to_excel(st.session_state.df_helpdesk, "Helpdesk"), "Master_Helpdesk.xlsx")

elif menu == "5. ⚙️ Spare Part Management":
    st.header("Spare Part Management")
    if st.session_state.df_sparepart.empty:
        f_sp = st.file_uploader("Upload Data Spare Part", type=['xlsx'], key="up_sp")
        if f_sp:
            if st.button("🚀 Proses Data", type="primary"):
                f_sp.seek(0)
                st.session_state.df_sparepart = pd.read_excel(f_sp)
                st.rerun()
    if not st.session_state.df_sparepart.empty:
        edited_sp = st.data_editor(st.session_state.df_sparepart, num_rows="dynamic", use_container_width=True, height=400)
        col1, col2 = st.columns([1, 4])
        with col1:
            if st.button("💾 Simpan Master Sparepart", use_container_width=True):
                st.session_state.df_sparepart = edited_sp
                st.session_state.flash = "✅ Data Spare Part Disimpan!"
                st.rerun()
        with col2:
            st.download_button("📥 Download Master Spare Part", convert_df_to_excel(st.session_state.df_sparepart, "Sparepart"), "Master_Sparepart.xlsx")

# ----------------------------------------
# MENU 6: PROGRAM NOP PBUN (BARU)
# ----------------------------------------
elif menu == "6. 📋 Program Nop Pbun":
    st.header("Manajemen Program Aktif & Activity")
    st.markdown("Rekapan kegiatan/program yang sedang berjalan di site-site seluruh regional. *Data di sini akan otomatis diselipkan ke dalam Site pada Peta di Menu 3.*")
    
    if st.session_state.df_program.empty:
        st.info("Upload file 'Program Nop Pbun.xlsx' Anda di bawah ini:")
        f_prog = st.file_uploader("Upload Data Program", type=['xlsx'], key="up_prog_menu")
        if f_prog:
            if st.button("🚀 Proses Master Program", type="primary"):
                f_prog.seek(0)
                st.session_state.df_program = parse_program_excel(f_prog)
                build_coverage.clear() # Reset memori peta agar program terbaru langsung masuk
                st.session_state.flash = "✅ Master Program Berhasil Diproses!"
                st.rerun()
                
    if not st.session_state.df_program.empty:
        edited_prog = st.data_editor(st.session_state.df_program, num_rows="dynamic", use_container_width=True, height=400)
        col1, col2 = st.columns([1, 4])
        with col1:
            if st.button("💾 Simpan Master Program", use_container_width=True):
                st.session_state.df_program = edited_prog
                build_coverage.clear()
                st.session_state.flash = "✅ Master Program Berhasil Diperbarui!"
                st.rerun()
        with col2:
            st.download_button("📥 Download Master Program", convert_df_to_excel(st.session_state.df_program, "Program"), "Master_Program.xlsx")
