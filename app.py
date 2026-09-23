# ==========================================
# BAGIAN IMPORT 
# ==========================================
import os
import io
import time
import uuid
import hmac
import hashlib
import calendar
import datetime
import base64
import pytz

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import extra_streamlit_components as stx
import geopy.distance
from PIL import Image
from streamlit_js_eval import get_geolocation
from dotenv import load_dotenv

# Impor Firebase Firestore
import firebase_admin
from firebase_admin import credentials, firestore

# Impor Supabase untuk Storage saja
from supabase import create_client, Client

# ==========================================
# FUNGSI UTILITAS AWAL (Sangat Ringan)
# ==========================================
def kompres_foto(image_bytes, quality=50, max_size=(400, 400)):
    """Fungsi kompresi foto untuk menghemat Storage"""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.thumbnail(max_size)
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=quality, optimize=True)
        return output.getvalue()
    except Exception:
        return image_bytes 

load_dotenv()

# --- 1. INISIALISASI DATABASE FIREBASE ---
if not firebase_admin._apps:
    # Trik membaca Firebase Credentials dari Streamlit Secrets
    if not os.path.exists("firebase_credentials.json") and "FIREBASE_JSON" in st.secrets:
        with open("firebase_credentials.json", "w") as f:
            f.write(st.secrets["FIREBASE_JSON"])

    cred = credentials.Certificate("firebase_credentials.json")
    firebase_admin.initialize_app(cred)

db = firestore.client()

# --- 2. INISIALISASI SUPABASE STORAGE ---
url = os.environ.get("SUPABASE_URL") or st.secrets.get("SUPABASE_URL", "")
key = os.environ.get("SUPABASE_KEY") or st.secrets.get("SUPABASE_KEY", "")

try:
    supabase: Client = create_client(url, key)
except Exception as e:
    st.error(f"Gagal terhubung ke Supabase Storage: {e}")
    st.stop()

def upload_ke_supabase(file_bytes, file_path, content_type):
    bucket_name = "absensi-files"
    try:
        supabase.storage.from_(bucket_name).upload(
            path=file_path,
            file=file_bytes,
            file_options={"content-type": content_type, "upsert": "true"}
        )
        return supabase.storage.from_(bucket_name).get_public_url(file_path)
    except Exception as e:
        st.error(f"Gagal upload ke server Storage: {e}")
        return None

@st.dialog("Peringatan File CSV ⚠️")
def tampilkan_peringatan_csv():
    st.write("Gagal memproses file: Terdapat **sel atau baris kosong** di dalam file Anda.")
    st.write("Pastikan semua data terisi penuh dan hapus baris kosong di bagian paling bawah tabel, lalu coba upload ulang.")
    if st.button("Oke, Saya Mengerti", key="btn_close_dialog_csv", use_container_width=True):
        st.rerun()

# --- 3. FUNGSI KRIPTOGRAFI KEAMANAN ---
SECRET_KEY = os.environ.get("COOKIE_SECRET") or st.secrets.get("COOKIE_SECRET")
SUPERADMIN_PASSWORD = os.environ.get("SUPERADMIN_PASSWORD") or st.secrets.get("SUPERADMIN_PASSWORD")

if not SECRET_KEY or not SUPERADMIN_PASSWORD:
    st.error("🔒 KUNCI RAHASIA TIDAK DITEMUKAN! Pastikan COOKIE_SECRET dan SUPERADMIN_PASSWORD terisi.")
    st.stop()

def generate_signed_token(role_name: str) -> str:
    signature = hmac.new(SECRET_KEY.encode(), role_name.encode(), hashlib.sha256).hexdigest()
    return f"{role_name}|{signature}"

def verify_and_get_role(token: str):
    if not token or "|" not in token:
        return None
    parts = token.split("|", 1)
    role_name, client_signature = parts[0], parts[1]
    expected_signature = hmac.new(SECRET_KEY.encode(), role_name.encode(), hashlib.sha256).hexdigest()
    if hmac.compare_digest(client_signature, expected_signature):
        return role_name
    return None

# --- 4. KONFIGURASI HALAMAN & COOKIE ---
st.set_page_config(page_title="Sistem Absensi Sekolah Cabdis Wil IV", page_icon="🏫", layout="centered")
cookie_manager = stx.CookieManager(key="cookie_manager_utama")

# --- 5. CUSTOM CSS ---
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;600;700&display=swap');
    html, body, [class*="css"] { font-family: 'Poppins', sans-serif !important; }
    header {visibility: hidden !important; height: 0px !important;} 
    [data-testid="stToolbar"], [data-testid="stDecoration"], footer, #MainMenu {visibility: hidden !important;}
    [data-testid="stHeaderActionElements"], .header-anchor { display: none !important; }
    .block-container { padding-top: 2rem !important; }
    .stForm, div[data-testid="stExpander"] {
        background-color: #FFFFFF; padding: 24px; border-radius: 12px;
        box-shadow: 0px 4px 15px rgba(0, 0, 0, 0.05); border: 1px solid #E2E8F0;
    }
    div.stButton > button {
        background-color: #2563EB !important; color: white !important; font-weight: 600 !important;
        border-radius: 8px !important; border: none !important; transition: 0.3s;
    }
    div.stButton > button:hover { background-color: #1D4ED8 !important; box-shadow: 0 4px 10px rgba(37, 99, 235, 0.3); }
    [data-testid="stSidebar"] { background-color: #F8FAFC !important; border-right: 1px solid #E2E8F0; }
    </style>
""", unsafe_allow_html=True)

# --- 6. FUNGSI INTERAKSI FIREBASE FIRESTORE ---
def get_data_sekolah():
    try:
        docs = db.collection('sekolah').stream()
        data = [doc.to_dict() for doc in docs]
        if data: return pd.DataFrame(data)
    except: pass
    return pd.DataFrame(columns=['school_name', 'lat', 'lng', 'radius_m'])

def get_data_pegawai():
    try:
        docs = db.collection('pegawai').stream()
        data = []
        for doc in docs:
            d = doc.to_dict()
            data.append({
                'nip': str(d.get('nip', '')),
                'name': d.get('name', ''),
                'school_name': d.get('school_name', ''),
                'photo_uploaded': d.get('photo_uploaded', False),
                'is_cadar': d.get('is_cadar', False)
            })
        if data:
            df = pd.DataFrame(data)
            df['nip'] = df['nip'].astype(str)
            return df
    except: pass
    return pd.DataFrame(columns=['nip', 'name', 'school_name', 'photo_uploaded', 'is_cadar'])

def get_data_admin():
    try:
        docs = db.collection('admins').stream()
        data = []
        for doc in docs:
            d = doc.to_dict()
            d['id'] = doc.id
            data.append(d)
        if data: return pd.DataFrame(data)
    except: pass
    return pd.DataFrame(columns=['id', 'username', 'password', 'sekolah'])

def get_data_pengaturan():
    try:
        docs = db.collection('pengaturan').limit(1).stream()
        data = [doc.to_dict() for doc in docs]
        if data:
            return pd.DataFrame(data)
        else:
            default_data = {'batas_masuk': '07:30', 'batas_pulang': '16:00'}
            db.collection('pengaturan').add(default_data)
            return pd.DataFrame([default_data])
    except:
        return pd.DataFrame([{'batas_masuk': '07:30', 'batas_pulang': '16:00'}])

# --- 7. INISIALISASI SESSION STATE ---
for key_state, val in {'role': None, 'admin_sekolah': "Semua Sekolah", 'logout_triggered': False, 'wajah_terverifikasi': False}.items():
    if key_state not in st.session_state: st.session_state[key_state] = val

if 'schools' not in st.session_state: st.session_state.schools = get_data_sekolah()
if 'employees' not in st.session_state: st.session_state.employees = get_data_pegawai()
if 'settings' not in st.session_state: st.session_state.settings = get_data_pengaturan()

raw_token = cookie_manager.get("auth_token")
saved_admin_school = cookie_manager.get("admin_sekolah")
if saved_admin_school: st.session_state.admin_sekolah = saved_admin_school

valid_role = verify_and_get_role(raw_token)

if st.session_state.role is not None:
    st.session_state.logout_triggered = False
elif st.session_state.logout_triggered:
    if raw_token is None: st.session_state.logout_triggered = False 
else:
    if valid_role: st.session_state.role = valid_role
    elif raw_token and not valid_role:
        st.session_state.role = None
        try:
            cookie_manager.delete("auth_token", key="del_auth_invalid_role")
            cookie_manager.delete("admin_sekolah", key="del_sch_invalid_role")
        except: pass

def logout():
    st.session_state.role = None
    st.session_state.admin_sekolah = "Semua Sekolah"
    st.session_state.logout_triggered = True 
    st.session_state.wajah_terverifikasi = False 
    try:
        cookie_manager.delete("auth_token", key="delete_auth_token_btn")
        cookie_manager.delete("role", key="delete_role_btn") 
        cookie_manager.delete("admin_sekolah", key="delete_admin_sekolah_btn") 
    except: pass

# ==========================================
# HALAMAN LOGIN UTAMA
# ==========================================
if st.session_state.role is None:
    st.title("📍 Portal Presensi Sekolah CABDIS WIL IV")
    st.info("Selamat datang! Untuk merekam kehadiran Anda, silakan klik tombol di bawah ini.")

    if st.button("📸 Mulai Presensi Wajah & GPS", type="primary", use_container_width=True, key="btn_login_pegawai_main"):
        st.session_state.role = "Pegawai"
        st.session_state.wajah_terverifikasi = False
        cookie_manager.set("auth_token", generate_signed_token("Pegawai"), key="set_token_login_pegawai")
        time.sleep(0.5)
        st.rerun()

    st.write("---")
    st.caption("Akses khusus Pengelola Sistem:")
    col_admin, col_super = st.columns(2)

    with col_admin:
        with st.expander("🔑 Login Admin"):
            input_user_admin = st.text_input("Username Admin:", key="user_admin_main")
            pwd = st.text_input("Password Admin:", type="password", key="pwd_admin_main")
            if st.button("Masuk Admin", use_container_width=True, key="btn_admin_main"):
                docs = db.collection('admins').where('username', '==', input_user_admin).where('password', '==', pwd).stream()
                match = [d.to_dict() for d in docs]
                if match: 
                    st.session_state.role = "Admin"
                    assigned_school = match[0].get('sekolah', 'Semua Sekolah')
                    st.session_state.admin_sekolah = assigned_school
                    cookie_manager.set("auth_token", generate_signed_token("Admin"), key="set_token_login_admin")
                    cookie_manager.set("admin_sekolah", assigned_school, key="set_sch_login_admin")
                    time.sleep(0.5)
                    st.rerun()
                else: st.error("Username atau Password Salah!")

    with col_super:
        with st.expander("🛠️ Login Superadmin"):
            pwd_super = st.text_input("Password Superadmin:", type="password", key="pwd_super_main")
            if st.button("Masuk Superadmin", use_container_width=True, key="btn_super_main"):
                if pwd_super == SUPERADMIN_PASSWORD:
                    st.session_state.role = "Superadmin"
                    cookie_manager.set("auth_token", generate_signed_token("Superadmin"), key="set_token_login_super")
                    time.sleep(0.5)
                    st.rerun()
                else: st.error("Password Salah!")
    st.stop()

# ==========================================
# SIDEBAR
# ==========================================
st.sidebar.title("Informasi Akun")
st.sidebar.success(f"Akses: **{st.session_state.role}**")
if st.session_state.role == "Admin": st.sidebar.caption(f"Unit Kerja: {st.session_state.admin_sekolah}")
st.sidebar.button("🚪 Keluar (Logout)", on_click=logout, key="btn_logout_sidebar")
st.sidebar.write("---")
waktu_sekarang = datetime.datetime.now(pytz.timezone('Asia/Makassar'))
st.sidebar.markdown("**Waktu Server (WITA):**")
st.sidebar.info(f"🕒 {waktu_sekarang.strftime('%H:%M:%S')} WITA\n\n📅 {waktu_sekarang.strftime('%d-%m-%Y')}")
st.sidebar.caption("Jam ini yang akan terekam di absensi.")
st.sidebar.write("---")

# ==========================================
# HAK AKSES 1: PEGAWAI
# ==========================================
if st.session_state.role == "Pegawai":
    st.button("⬅️ Kembali ke Halaman Awal", on_click=logout, key="btn_back_pegawai")
    st.title("📍 Presensi GPS & Wajah")
    nip_input = st.text_input("SILAHKAN KETIK NIP:", placeholder="Contoh: 198001012005011001", key="nip_input_pegawai")
    
    if nip_input.strip():
        try:
            docs_pegawai = db.collection('pegawai').where('nip', '==', str(nip_input.strip())).stream()
            data_kandidat = [d.to_dict() for d in docs_pegawai]
            df_kandidat = pd.DataFrame(data_kandidat) if data_kandidat else pd.DataFrame()
        except: df_kandidat = pd.DataFrame()
            
        if df_kandidat.empty:
            st.warning("⚠️ Data pegawai tidak ditemukan.")
        else:
            emp_data = df_kandidat.iloc[0]
            try: sch_data = st.session_state.schools[st.session_state.schools['school_name'] == emp_data['school_name']].iloc[0]
            except:
                st.error("Data sekolah untuk pegawai ini tidak ditemukan.")
                st.stop()
                
            curr_dev_cookie = cookie_manager.get("school_device_token")
            try:
                docs_dev = db.collection('perangkat_sekolah').where('school_name', '==', sch_data['school_name']).stream()
                list_dev = [d.to_dict() for d in docs_dev]
                total_terdaftar = len(list_dev)
            except: total_terdaftar = 0

            is_valid_pc = False
            if total_terdaftar > 0:
                try:
                    docs_valid = db.collection('perangkat_sekolah').where('school_name', '==', sch_data['school_name']).where('device_id', '==', str(curr_dev_cookie)).stream()
                    if len(list(docs_valid)) > 0: is_valid_pc = True
                except: pass
                
                if not is_valid_pc:
                    st.error("⛔ AKSES DITOLAK! Perangkat ini belum terdaftar sebagai PC Resmi Sekolah.")
                    st.stop()
                else: st.success("🖥️ Perangkat Terverifikasi: PC Resmi Sekolah.")
            else:
                st.warning("⚠️ Sekolah ini belum mendaftarkan PC Resmi. Presensi di perangkat apapun masih terbuka.")
                
            st.info(f"👤 Nama: **{emp_data['name']}**\n\n🏫 Anda ditugaskan di: **{sch_data['school_name']}**")
            loc = get_geolocation()
            
            if loc:
                user_lat, user_lng = loc['coords']['latitude'], loc['coords']['longitude']
                jarak_meter = geopy.distance.geodesic((user_lat, user_lng), (sch_data['lat'], sch_data['lng'])).meters
                
                if jarak_meter <= sch_data['radius_m']:
                    st.success(f"✅ Lokasi Valid! Jarak: {jarak_meter:.0f} meter dari pusat.")
                    
                    is_uploaded = str(emp_data.get('photo_uploaded', 'False')).lower() == 'true'
                    is_cadar = str(emp_data.get('is_cadar', 'False')).lower() == 'true'
                    
                    if not is_cadar and not (is_uploaded and pd.notna(emp_data.get('photo_base64'))):
                        st.warning("⚠️ Admin belum mengunggah foto acuan wajah Anda.")
                    else:
                        img_camera = st.camera_input("Ambil Foto di Lokasi", key="cam_pegawai_input")
                        
                        if is_cadar:
                            st.session_state.wajah_terverifikasi = True
                            st.info("🧕 Verifikasi biometrik dilewati (Mode Audit).")

                        if img_camera:
                            bytes_data = kompres_foto(img_camera.getvalue())
                            cam_base64 = f"data:image/jpeg;base64,{base64.b64encode(bytes_data).decode('utf-8')}"
                            tgl_sekarang_str = datetime.datetime.now(pytz.timezone('Asia/Makassar')).strftime('%Y%m%d_%H%M%S')
                            url_foto_harian = upload_ke_supabase(bytes_data, f"foto_presensi/{emp_data['nip']}_{tgl_sekarang_str}.jpg", "image/jpeg")
                            
                            if not is_cadar:
                                html_code = f"""
                                <!DOCTYPE html>
                                <html>
                                <head><script src="https://cdn.jsdelivr.net/npm/@vladmandic/face-api@1.7.12/dist/face-api.js"></script></head>
                                <body style="text-align: center; font-family: sans-serif; margin:0; padding:5px;">
                                    <div id="status" style="color:#d9534f; font-weight:bold;">Memuat AI...</div>
                                    <div id="kode" style="display:none; color:white; background:#5cb85c; padding:8px; border-radius:5px; font-weight:bold;">✅ WAJAH COCOK</div>
                                    <img id="refImg" crossorigin="anonymous" src="{emp_data.get('photo_base64', '')}" style="display:none;" />
                                    <img id="camImg" src="{cam_base64}" style="display:none;" />
                                    <script>
                                        async function runAI() {{
                                            const status = document.getElementById('status');
                                            try {{
                                                const URL = 'https://cdn.jsdelivr.net/npm/@vladmandic/face-api@1.7.12/model';
                                                await faceapi.nets.ssdMobilenetv1.loadFromUri(URL);
                                                await faceapi.nets.faceLandmark68Net.loadFromUri(URL);
                                                await faceapi.nets.faceRecognitionNet.loadFromUri(URL);
                                                const ref = await faceapi.detectSingleFace(document.getElementById('refImg')).withFaceLandmarks().withFaceDescriptor();
                                                const cam = await faceapi.detectSingleFace(document.getElementById('camImg')).withFaceLandmarks().withFaceDescriptor();
                                                if(!ref || !cam) {{ status.innerText = "⚠️ Wajah tidak jelas."; return; }}
                                                const match = new faceapi.FaceMatcher(ref).findBestMatch(cam.descriptor);
                                                if(match.distance <= 0.5) {{ 
                                                    status.style.display = "none";
                                                    document.getElementById('kode').style.display = "inline-block";
                                                    try {{
                                                        window.parent.document.querySelectorAll('p').forEach(p => {{
                                                            if(p.innerText === "V_E_R_I_F_I_E_D") p.closest('button').click();
                                                        }});
                                                    }} catch(err) {{}}
                                                }} else {{ status.innerText = "⛔ WAJAH TIDAK COCOK!"; }}
                                            }} catch(e) {{ status.innerText = "Gagal memuat AI."; }}
                                        }}
                                        setTimeout(runAI, 500);
                                    </script>
                                </body>
                                </html>
                                """
                                components.html(html_code, height=60, scrolling=False)

                                if not st.session_state.wajah_terverifikasi:
                                    st.markdown('<style>div.stButton > button:has(p:contains("V_E_R_I_F_I_E_D")) {opacity: 0; height: 1px; pointer-events: none;}</style>', unsafe_allow_html=True)
                                    if st.button("V_E_R_I_F_I_E_D", key="btn_hidden_trigger"):
                                        st.session_state.wajah_terverifikasi = True
                                        st.rerun()
                                    
                            if st.session_state.wajah_terverifikasi:
                                col_masuk, col_pulang = st.columns(2)
                                btn_masuk = col_masuk.button("📥 MASUK", type="primary", use_container_width=True, key="btn_absen_masuk")
                                btn_pulang = col_pulang.button("📤 PULANG", use_container_width=True, key="btn_absen_pulang")
                                    
                                if btn_masuk or btn_pulang:
                                    now = datetime.datetime.now(pytz.timezone('Asia/Makassar'))
                                    tgl_sekarang = now.strftime('%Y-%m-%d')
                                    jenis_aksi = "Masuk" if btn_masuk else "Pulang"
                                    
                                    try:
                                        docs_absen = db.collection('absensi').where('nip', '==', str(emp_data['nip'])).where('tanggal', '==', tgl_sekarang).stream()
                                        abs_list = [d.to_dict() for d in docs_absen]
                                        df_absen_hari_ini = pd.DataFrame(abs_list) if abs_list else pd.DataFrame()
                                    except: df_absen_hari_ini = pd.DataFrame()
                                    
                                    if not df_absen_hari_ini.empty and not df_absen_hari_ini[df_absen_hari_ini['status'].str.contains(jenis_aksi, na=False, case=False)].empty:
                                        st.warning(f"⚠️ Anda sudah absen **{jenis_aksi}** hari ini!")
                                    else:
                                        jam_sekarang = now.time()
                                        try:
                                            b_masuk_str = st.session_state.settings['batas_masuk'].iloc[0] if not st.session_state.settings.empty else '07:30'
                                            b_pulang_str = st.session_state.settings['batas_pulang'].iloc[0] if not st.session_state.settings.empty else '16:00'
                                            batas_masuk_obj = datetime.datetime.strptime(b_masuk_str, '%H:%M').time()
                                            batas_pulang_obj = datetime.datetime.strptime(b_pulang_str, '%H:%M').time()
                                        except:
                                            batas_masuk_obj, batas_pulang_obj = datetime.time(7, 30), datetime.time(16, 0)
                                        
                                        if btn_masuk:
                                            jenis_absen = "Masuk (TERLAMBAT)" if jam_sekarang > batas_masuk_obj else "Masuk (Tepat Waktu)"
                                        else:
                                            jenis_absen = "Pulang (LEBIH AWAL)" if jam_sekarang < batas_pulang_obj else "Pulang (Tepat Waktu)"

                                        status_final = f"Hadir {'[Audit] ' if is_cadar else ''}- {jenis_absen}"
                                        
                                        db.collection('absensi').add({
                                            'nip': str(emp_data['nip']), 'nama': emp_data['name'], 
                                            'sekolah': sch_data['school_name'], 'tanggal': tgl_sekarang, 
                                            'jam': now.strftime('%H:%M:%S'), 'jarak_m': str(round(jarak_meter, 1)), 
                                            'status': status_final, 'foto_bukti': url_foto_harian if url_foto_harian else "" 
                                        })
                                        st.success(f"✅ Absensi {jenis_absen} berhasil!")
                            else: st.warning("Tunggu verifikasi biometrik selesai...")
                else: st.error(f"⛔ Anda berada di luar radius ({jarak_meter:.0f} m dari {sch_data['radius_m']} m).")
            else: st.warning("Menunggu akses GPS...")

# ==========================================
# HAK AKSES 2: ADMIN
# ==========================================
elif st.session_state.role == "Admin":
    col_judul, col_tombol = st.columns([3, 1])
    col_judul.title("🔐 Dashboard Admin")
    col_tombol.button("🚪 Logout", on_click=logout, use_container_width=True, key="btn_logout_top_admin")
    admin_akses = st.session_state.get('admin_sekolah', 'Semua Sekolah')

    # --- 1. KELOLA PC ABSENSI ---
    st.markdown("### 🖥️ 1. Kelola PC Absensi Sekolah")
    with st.expander("📌 Pendaftaran & Daftar PC", expanded=True):
        try:
            docs_pc = db.collection('perangkat_sekolah').where('school_name', '==', admin_akses).stream()
            list_pc = [{'id': d.id, **d.to_dict()} for d in docs_pc]
        except: list_pc = []

        total_terdaftar = len(list_pc)
        st.markdown("##### ➕ Daftarkan PC Ini")
        st.caption(f"Status Kuota Perangkat: **{total_terdaftar} dari 2 PC Terdaftar**")

        if total_terdaftar >= 2:
            st.warning("🔒 **PENDAFTARAN TERKUNCI!** Hubungi Superadmin.")
        else:
            nama_pc_input = st.text_input("Nama/Label PC", key="inp_nama_pc_baru")
            if st.button("📌🛠️ Daftarkan PC Ini", key="btn_register_pc_dynamic"):
                if admin_akses == "Semua Sekolah": st.error("Login spesifik sebagai admin sekolah diperlukan.")
                elif nama_pc_input.strip():
                    new_token = str(uuid.uuid4())
                    cookie_manager.set("school_device_token", new_token, key="set_pc_cookie_dyn")
                    db.collection('perangkat_sekolah').add({
                        'school_name': admin_akses, 
                        'device_id': new_token, 
                        'device_name': nama_pc_input.strip()
                    })
                    st.success("✅ PC berhasil didaftarkan!")
                    time.sleep(1)
                    st.rerun()
                else: st.error("Masukkan label PC.")

        st.markdown("##### 📋 Daftar PC Terdaftar")
        if list_pc:
            for r_pc in list_pc: st.write(f"🖥️ **{r_pc['device_name']}** - 🔒 Terkunci")
        else: st.info("Belum ada PC terdaftar.")
    
    # --- 2. KELOLA FOTO ACUAN ---
    st.markdown("### 📸 2. Kelola Foto Acuan")
    col_f1, col_f2 = st.columns(2)
    opsi_sekolah_foto = ["Semua Sekolah"] + st.session_state.schools['school_name'].tolist()
    sekolah_pilihan_foto = col_f1.selectbox("🏢 Filter Sekolah:", opsi_sekolah_foto if admin_akses == "Semua Sekolah" else [admin_akses], disabled=(admin_akses != "Semua Sekolah"))
    search_query_foto = col_f2.text_input("🔍 Cari NIP atau Nama:", key="search_admin_foto")
    
    if search_query_foto.strip():
        try:
            if sekolah_pilihan_foto != "Semua Sekolah":
                docs_peg = db.collection('pegawai').where('school_name', '==', sekolah_pilihan_foto).stream()
            else:
                docs_peg = db.collection('pegawai').stream()
            
            data_all_peg = [d.to_dict() for d in docs_peg]
            if data_all_peg:
                df_all = pd.DataFrame(data_all_peg)
                q_lower = search_query_foto.lower()
                mask = df_all['nip'].astype(str).str.lower().str.contains(q_lower, na=False) | df_all['name'].astype(str).str.lower().str.contains(q_lower, na=False)
                df_kandidat = df_all[mask]
            else: df_kandidat = pd.DataFrame()
        except: df_kandidat = pd.DataFrame()
            
        if not df_kandidat.empty:
            for index, emp in df_kandidat.iterrows():
                nip, nama, is_cadar, is_uploaded = str(emp['nip']), emp['name'], str(emp.get('is_cadar', 'False')).lower() == 'true', str(emp.get('photo_uploaded', False)).lower() == 'true'
                status_simbol = "🧕" if is_cadar else ("🟢" if is_uploaded else "🔴")
                with st.expander(f"{status_simbol} {nama} — NIP: {nip}"):
                    col_kiri, col_kanan = st.columns([1, 2])
                    if is_uploaded and pd.notna(emp.get('photo_base64')) and emp['photo_base64']: col_kiri.image(emp['photo_base64'], use_container_width=True)
                    else: col_kiri.info("📷 Belum ada foto")
                            
                    col_kanan.markdown(f"**Unit:** {emp['school_name']}")
                    if is_uploaded: col_kanan.error("🔒 Foto terkunci.")
                    else:
                        foto = col_kanan.file_uploader("Upload Foto", type=['jpg', 'jpeg', 'png'], key=f"foto_up_{nip}")
                        if foto and col_kanan.button("💾 Simpan", key=f"btn_save_foto_{nip}", use_container_width=True):
                            file_bytes = kompres_foto(foto.getvalue(), quality=60, max_size=(600, 600))
                            url_foto = upload_ke_supabase(file_bytes, f"foto_acuan/{nip}.jpg", "image/jpeg")
                            if url_foto:
                                docs_to_update = db.collection('pegawai').where('nip', '==', nip).stream()
                                for doc_u in docs_to_update:
                                    doc_u.reference.update({
                                        'photo_uploaded': True, 
                                        'photo_base64': url_foto
                                    })
                                st.session_state.employees = get_data_pegawai()
                                st.rerun()

    # --- 3. REKAP HARIAN ---
    st.markdown("### 📋 3. Rekap Harian")
    with st.form("form_filter_rekap"):
        tgl_pilihan = st.date_input("Tanggal:")
        opsi_sekolah = ["-- Pilih Sekolah --", "Semua Sekolah"] + st.session_state.schools['school_name'].tolist()
        sekolah_pilihan = st.selectbox("Sekolah:", opsi_sekolah if admin_akses == "Semua Sekolah" else [admin_akses], disabled=(admin_akses != "Semua Sekolah"))
        if st.form_submit_button("📊 TAMPILKAN"): st.session_state.show_data_rekap = (sekolah_pilihan != "-- Pilih Sekolah --")

    if st.session_state.get('show_data_rekap', False):
        df_emp = st.session_state.employees.copy()
        if sekolah_pilihan != "Semua Sekolah": df_emp = df_emp[df_emp['school_name'] == sekolah_pilihan]
        
        if not df_emp.empty:
            tgl_str = tgl_pilihan.strftime('%Y-%m-%d')
            try:
                docs_absen = db.collection('absensi').where('tanggal', '==', tgl_str).stream()
                data_abs = [d.to_dict() for d in docs_absen]
                df_absen_tgl = pd.DataFrame(data_abs) if data_abs else pd.DataFrame()
            except: df_absen_tgl = pd.DataFrame()
            
            rekap_list = []
            for _, emp in df_emp.iterrows():
                nip = str(emp['nip'])
                data_absen = df_absen_tgl[df_absen_tgl['nip'] == nip] if not df_absen_tgl.empty else pd.DataFrame()
                jam_masuk = jam_pulang = jarak = '-'
                status_final = 'Tanpa Keterangan'
                
                if not data_absen.empty:
                    am = data_absen[data_absen['status'].str.contains('Masuk', na=False, case=False)]
                    ap = data_absen[data_absen['status'].str.contains('Pulang', na=False, case=False)]
                    al = data_absen[~data_absen['status'].str.contains('Hadir|Masuk|Pulang', na=False, case=False)]
                    
                    if not am.empty: jam_masuk, jarak, status_final = am.iloc[0]['jam'], am.iloc[0]['jarak_m'], am.iloc[0]['status']
                    if not ap.empty: 
                        jam_pulang = ap.iloc[0]['jam']
                        if jarak == '-': jarak = ap.iloc[0]['jarak_m']
                        status_final = f"{status_final} & {ap.iloc[0]['status']}" if not am.empty else ap.iloc[0]['status']
                    if not al.empty: status_final, jarak = al.iloc[0]['status'], al.iloc[0]['jarak_m']
                        
                rekap_list.append({'NIP': nip, 'NAMA': emp['name'], 'SEKOLAH': emp['school_name'], 'TANGGAL': tgl_str, 'JARAK': str(jarak), 'MASUK': jam_masuk, 'PULANG': jam_pulang, 'STATUS': status_final})
                
            df_rekap = pd.DataFrame(rekap_list)
            st.dataframe(df_rekap, use_container_width=True)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer: df_rekap.to_excel(writer, index=False)
            st.download_button("📥 Download Excel", buffer.getvalue(), f"Rekap_{tgl_str}.xlsx")
            
    # --- 4. REKAP BULANAN ---
    st.markdown("### 📊 4. Rekap Bulanan")
    col_rek1, col_rek2 = st.columns(2)
    filter_sch_rekap = col_rek1.selectbox("Pilih Sekolah:", ["-- Pilih Sekolah --"] + st.session_state.schools['school_name'].tolist() if admin_akses == "Semua Sekolah" else [admin_akses], disabled=(admin_akses != "Semua Sekolah"))
    month_options = [(datetime.datetime.now() - datetime.timedelta(days=30*i)).strftime('%Y-%m') for i in range(12)]
    filter_bln_rekap = col_rek2.selectbox("Bulan:", month_options)
        
    if st.button("📈 Tampilkan Rekap Bulanan", type="primary"):
        if filter_sch_rekap != "-- Pilih Sekolah --":
            with st.spinner("Menghitung kalkulasi..."):
                try:
                    docs_p = db.collection('pegawai').where('school_name', '==', filter_sch_rekap).stream()
                    data_p = [d.to_dict() for d in docs_p]
                    
                    docs_a = db.collection('absensi').where('sekolah', '==', filter_sch_rekap).stream()
                    all_abs = [d.to_dict() for d in docs_a]
                    data_a = [a for a in all_abs if str(a.get('tanggal', '')).startswith(filter_bln_rekap)]
                    
                    if data_p:
                        rekap_data = {str(p['nip']): {'NIP': str(p['nip']), 'NAMA': p['name'], 'MENIT TERLAMBAT': 0, 'MENIT CEPAT PULANG': 0, 'JUMLAH KEHADIRAN': 0, 'TANPA KETERANGAN': 0, 'SAKIT': 0, 'DINAS LUAR': 0, 'CUTI': 0} for p in data_p}
                        
                        df_rekap = pd.DataFrame(list(rekap_data.values()))
                        st.dataframe(df_rekap, use_container_width=True)
                        buffer = io.BytesIO()
                        with pd.ExcelWriter(buffer, engine='openpyxl') as writer: df_rekap.to_excel(writer, index=False)
                        st.download_button("📥 Download Excel", buffer.getvalue(), f"Rekap_{filter_bln_rekap}.xlsx")
                except Exception as e: st.error(f"Gagal memuat rekap: {e}")

# ==========================================
# HAK AKSES 3: SUPERADMIN
# ==========================================
elif st.session_state.role == "Superadmin":
    col_judul, col_tombol = st.columns([3, 1])
    col_judul.title("🛠️ Dashboard Superadmin")
    col_tombol.button("🚪 Logout", on_click=logout, use_container_width=True)
    
    tab1, tab_pc, tab2, tab3, tab4, tab5, tab6 = st.tabs(["🏛️ Sekolah", "💻 PC", "👥 Pegawai", "🔑 Admin", "📝 Izin", "🚨 Database", "⚙️ Jam"])
    
    with tab1:
        st.markdown("### Sekolah Aktif")
        edited_schools = st.data_editor(st.session_state.schools, num_rows="dynamic", use_container_width=True)
        if st.button("💾 Simpan Perubahan Sekolah", type="primary"):
            records = edited_schools.to_dict(orient='records')
            for rec in records:
                db.collection('sekolah').document(rec['school_name']).set(rec, merge=True)
            st.session_state.schools = get_data_sekolah()
            st.rerun()

    with tab_pc:
        st.markdown("### Buka Kunci PC")
        sekolah_pilihan_pc = st.selectbox("Filter Sekolah:", ["Semua Sekolah"] + st.session_state.schools['school_name'].tolist())
        try:
            if sekolah_pilihan_pc != "Semua Sekolah":
                docs_pc = db.collection('perangkat_sekolah').where('school_name', '==', sekolah_pilihan_pc).stream()
            else:
                docs_pc = db.collection('perangkat_sekolah').stream()
            
            list_pc_super = [{'id': d.id, **d.to_dict()} for d in docs_pc]
            if list_pc_super:
                for r_pc in list_pc_super:
                    c1, c2, c3 = st.columns([2, 2, 1])
                    c1.write(r_pc.get('school_name', '')); c2.write(r_pc.get('device_name', ''))
                    if c3.button("🔓 Hapus Kunci", key=f"del_{r_pc['id']}"):
                        db.collection('perangkat_sekolah').document(r_pc['id']).delete()
                        st.rerun()
        except: pass

    with tab2:
        st.markdown("### Upload Pegawai Massal (CSV/Excel)")
        file_upload = st.file_uploader("Upload Excel", type=['xlsx', 'xls'])
        if file_upload and st.button("Proses Upload"):
            try:
                df_upload = pd.read_excel(file_upload, dtype=str).dropna(subset=['nip', 'name', 'school_name'], how='all')
                for _, r in df_upload.iterrows():
                    nip_str = str(r['nip']).strip()
                    db.collection('pegawai').document(nip_str).set({
                        'nip': nip_str,
                        'name': str(r['name']).strip(),
                        'school_name': str(r['school_name']).strip(),
                        'photo_uploaded': False,
                        'is_cadar': False
                    }, merge=True)
                st.session_state.employees = get_data_pegawai()
                st.success("✅ Berhasil upload pegawai!")
                st.rerun()
            except Exception as e: st.error(f"Gagal: {e}")

    with tab3:
        st.markdown("### Kelola Admin")
        df_admins = get_data_admin()
        for idx, row in df_admins.iterrows():
            with st.expander(f"👤 {row['username']} - {row['sekolah']}"):
                if st.button("🗑️ Hapus Admin", key=f"del_adm_{idx}"):
                    db.collection('admins').document(row['id']).delete()
                    st.rerun()

    with tab4:
        st.markdown("### Input Izin / Surat (Bypass)")
        nip_input_izin = st.text_input("NIP Pegawai:")
        if st.button("Input Surat Kosong/Izin") and nip_input_izin:
            db.collection('absensi').add({
                'nip': nip_input_izin, 
                'nama': 'Manual', 
                'sekolah': 'Manual', 
                'tanggal': datetime.datetime.now().strftime('%Y-%m-%d'), 
                'jam': '-', 
                'status': 'Izin'
            })
            st.success("Izin dicatat!")

    with tab5:
        st.markdown("### 🚨 Database Clean Up")
        if st.button("🖼️ Hapus Semua Foto (Teks Aman)", type="primary"):
            docs_a = db.collection('absensi').stream()
            for d in docs_a:
                if d.to_dict().get('foto_bukti'):
                    d.reference.update({'foto_bukti': ''})
            st.success("Foto fisik berhasil diputus dari database (Hemat Egress).")

    with tab6:
        st.markdown("### ⚙️ Jam Kerja")
        b_in = st.session_state.settings['batas_masuk'].iloc[0] if not st.session_state.settings.empty else '07:30'
        b_out = st.session_state.settings['batas_pulang'].iloc[0] if not st.session_state.settings.empty else '16:00'
        n_in = st.time_input("Batas Masuk", datetime.datetime.strptime(b_in, '%H:%M').time())
        n_out = st.time_input("Batas Pulang", datetime.datetime.strptime(b_out, '%H:%M').time())
        if st.button("Simpan Pengaturan"):
            docs_s = db.collection('pengaturan').limit(1).stream()
            for d in docs_s:
                d.reference.update({
                    'batas_masuk': n_in.strftime('%H:%M'), 
                    'batas_pulang': n_out.strftime('%H:%M')
                })
            st.session_state.settings = get_data_pengaturan()
            st.rerun()
