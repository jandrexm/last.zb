# lastzb.py
# tudo abaixo foi o que consegui resgatar do projeto antigo.

# 'dataset' é a tabela de entrada vindo do Power BI
import pandas as pd
import requests
import base64
import time
import random
import os
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- CONFIGURAÇÃO ---
CLIENT_ID = '3b5bef93fd9b42db9b996e26346e2f69'
CLIENT_SECRET = '4c45d44122954b0a9c6490aa605abd9f'
MAX_WORKERS = 10 

# CACHE
CACHE_DIR = r'C:\Users\jandr\OneDrive\Documents'
# ### MUDANÇA OBRIGATÓRIA: Mudei para v4 para IGNORAR o cache antigo incompleto e forçar nova busca
CACHE_FILE = os.path.join(CACHE_DIR, 'spotify_mb_cache_v4_features.csv') 
MB_USER_AGENT = {'User-Agent': 'PowerWrapped/1.5 (jandrexm@gmail.com)'}

# --- 0. PREPARAÇÃO ---
if not os.path.exists(CACHE_DIR):
    try: os.makedirs(CACHE_DIR)
    except: pass

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            df = pd.read_csv(CACHE_FILE, dtype=str)
            df['Key'] = df['Original_Artist'].fillna("").str.lower().str.strip() + "|" + \
                        df['Original_Track'].fillna("").str.lower().str.strip() + "|" + \
                        df['Original_Album'].fillna("").str.lower().str.strip()
            return df
        except: return pd.DataFrame()
    return pd.DataFrame()

df_cache = load_cache()

try:
    df_input = dataset.copy()
    df_input['Artist_Clean'] = df_input['Artist'].fillna("").astype(str).str.lower().str.strip()
    df_input['Track_Clean'] = df_input['Track'].fillna("").astype(str).str.lower().str.strip()
    df_input['Album_Clean'] = df_input['Album'].fillna("").astype(str).str.lower().str.strip()
    df_input['Key'] = df_input['Artist_Clean'] + "|" + df_input['Track_Clean'] + "|" + df_input['Album_Clean']
except NameError:
    df_input = pd.DataFrame()

# Verifica o que já tem no cache para não processar de novo
if not df_cache.empty and 'Key' in df_cache.columns:
    keys_existing = set(df_cache['Key'].unique())
    df_to_process = df_input[~df_input['Key'].isin(keys_existing)].copy()
else:
    df_to_process = df_input.copy()

rows_to_process = df_to_process.to_dict('records')

# --- 1. AUTENTICAÇÃO ---
token = None
if len(rows_to_process) > 0:
    def get_spotify_token():
        auth_str = f"{CLIENT_ID}:{CLIENT_SECRET}"
        b64_auth = base64.b64encode(auth_str.encode()).decode()
        headers = {'Authorization': f'Basic {b64_auth}'}
        data = {'grant_type': 'client_credentials'}
        try:
            # Token URL oficial
            r = requests.post('https://accounts.spotify.com/api/token', headers=headers, data=data, timeout=10)
            return r.json().get('access_token')
        except: return None
    token = get_spotify_token()

# --- 2. BUSCA MUSICBRAINZ ---
def search_musicbrainz(artist, track, album, track_mbid):
    base_url = "https://musicbrainz.org/ws/2"
    time.sleep(1.2) 

    if track_mbid and pd.notna(track_mbid) and str(track_mbid).strip() != "":
        try:
            url = f"{base_url}/recording/{track_mbid}"
            params = {'inc': 'releases+artist-credits', 'fmt': 'json'}
            r = requests.get(url, headers=MB_USER_AGENT, params=params, timeout=10)
            if r.status_code == 200:
                data = r.json()
                release = data['releases'][0] if 'releases' in data and data['releases'] else {}
                return {
                    'Official_Artist': data['artist-credit'][0]['name'] if 'artist-credit' in data else artist,
                    'Official_Track': data.get('title'),
                    'Official_Album': release.get('title'),
                    'Release_Date': release.get('date'),
                    'Duration_Ms': data.get('length'),
                    'Status': 'Found (MBID)',
                    'Source': 'MusicBrainz ID',
                    'Artist_Genres': None, 
                    'Track_Popularity': None,
                    'Is_Explicit': None,
                    'Track_ID': None
                }
        except: pass
    
    try:
        query = f'recording:"{track}" AND artist:"{artist}"'
        url = f"{base_url}/recording/"
        params = {'query': query, 'fmt': 'json', 'limit': 1}
        r = requests.get(url, headers=MB_USER_AGENT, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get('recordings'):
                hit = data['recordings'][0]
                release = hit['releases'][0] if 'releases' in hit and hit['releases'] else {}
                return {
                    'Official_Artist': hit['artist-credit'][0]['name'],
                    'Official_Track': hit.get('title'),
                    'Official_Album': release.get('title'),
                    'Release_Date': release.get('date'),
                    'Duration_Ms': hit.get('length'),
                    'Status': 'Found (MB Search)',
                    'Source': 'MusicBrainz Search',
                    'Artist_Genres': None,
                    'Track_Popularity': None,
                    'Is_Explicit': None,
                    'Track_ID': None
                }
    except: pass
    return None

# --- 3. BUSCA TRACK SPOTIFY ---
def enrich_track(row):
    track_orig = str(row['Track']).strip() if pd.notna(row['Track']) else ""
    artist_orig = str(row['Artist']).strip() if pd.notna(row['Artist']) else ""
    album_orig = str(row['Album']).strip() if pd.notna(row['Album']) else ""
    t_mbid = row.get('Track_MBID')
    
    result = {
        'Original_Artist': artist_orig, 'Original_Track': track_orig, 'Original_Album': album_orig,
        'Official_Artist': artist_orig, 'Official_Track': track_orig, 'Official_Album': album_orig,
        'Release_Date': None, 'Duration_Ms': None, 'Status': 'Not Found',
        'Cover_Image': None, 'Artist_ID': None, 'Track_ID': None,
        'Source': 'Original', 'Key': row['Key'],
        'Artist_Genres': None, 'Track_Popularity': None, 'Is_Explicit': None,
        'Valence': None, 'Energy': None, 'Danceability': None, 
        'Acousticness': None, 'Instrumentalness': None, 'Tempo': None
    }

    if not track_orig or not artist_orig: return result

    spotify_found = False
    
    if token:
        headers = {'Authorization': f'Bearer {token}'}
        # URL de Busca padrão
        url_search = "https://api.spotify.com/v1/search"
        
        def process_spotify_response(resp, method_name):
            if resp.status_code == 200:
                items = resp.json().get('tracks', {}).get('items', [])
                if items:
                    item = items[0]
                    alb = item['album']
                    rdate = alb['release_date']
                    if len(rdate) == 4: rdate += "-01-01"
                    elif len(rdate) == 7: rdate += "-01"
                    
                    imgs = alb['images']
                    img_url = imgs[1]['url'] if len(imgs) > 1 else (imgs[0]['url'] if imgs else None)
                    
                    result.update({
                        'Official_Artist': item['artists'][0]['name'],
                        'Official_Track': item['name'],
                        'Official_Album': alb['name'],
                        'Release_Date': rdate,
                        'Duration_Ms': item['duration_ms'],
                        'Status': 'Found',
                        'Cover_Image': img_url,
                        'Artist_ID': item['artists'][0]['id'],
                        'Track_ID': item['id'],
                        'Source': method_name,
                        'Track_Popularity': item.get('popularity'),
                        'Is_Explicit': item.get('explicit')
                    })
                    return True
            return False

        # Tenta: 1. Strict
        if album_orig:
            q = f"track:{track_orig} artist:{artist_orig} album:{album_orig}"
            try:
                r = requests.get(url_search, headers=headers, params={'q': q, 'type': 'track', 'limit': 1}, timeout=5)
                if process_spotify_response(r, 'Spotify (Strict)'): spotify_found = True
            except: pass

        # Tenta: 2. Flexível
        if not spotify_found:
            q = f"track:{track_orig} artist:{artist_orig}"
            try:
                r = requests.get(url_search, headers=headers, params={'q': q, 'type': 'track', 'limit': 1}, timeout=5)
                if process_spotify_response(r, 'Spotify (Track+Artist)'): spotify_found = True
            except: pass

        # Tenta: 3. Fuzzy
        if not spotify_found:
            q = f"{artist_orig} {track_orig}"
            try:
                r = requests.get(url_search, headers=headers, params={'q': q, 'type': 'track', 'limit': 1}, timeout=5)
                if process_spotify_response(r, 'Spotify (General)'): spotify_found = True
            except: pass

    if not spotify_found:
        mb_data = search_musicbrainz(artist_orig, track_orig, album_orig, t_mbid)
        if mb_data: result.update(mb_data)

    return result

# --- 4. EXECUÇÃO PARALELA (Tracks) ---
new_results = []
if len(rows_to_process) > 0:
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_row = {executor.submit(enrich_track, row): row for row in rows_to_process}
        for future in as_completed(future_to_row):
            new_results.append(future.result())

# --- 5. ENRIQUECIMENTOS EM LOTE ---
if new_results and token:
    
    # === 5.1 GÊNEROS (ARTISTAS) ===
    # Usa endpoint direto do Spotify
    artist_ids = list(set([r['Artist_ID'] for r in new_results if r['Artist_ID'] and r['Source'].startswith('Spotify')]))
    artist_meta = {} 
    
    for i in range(0, len(artist_ids), 50):
        chunk = artist_ids[i:i+50]
        ids_str = ','.join(chunk)
        try:
            url_art = f"https://api.spotify.com/v1/artists?ids={ids_str}"
            r = requests.get(url_art, headers={'Authorization': f'Bearer {token}'}, timeout=10)
            if r.status_code == 200:
                artists_data = r.json().get('artists', [])
                for art in artists_data:
                    if art:
                        genres = ", ".join(art.get('genres', []))
                        artist_meta[art['id']] = genres
        except: pass

    # === 5.2 AUDIO FEATURES (MÚSICAS) ===
    # Usa endpoint direto do Spotify: https://api.spotify.com/v1/audio-features
    track_ids = list(set([r['Track_ID'] for r in new_results if r.get('Track_ID') and r['Source'].startswith('Spotify')]))
    features_meta = {}

    for i in range(0, len(track_ids), 100):
        chunk = track_ids[i:i+100]
        ids_str = ','.join(chunk)
        try:
            url_feat = f"https://api.spotify.com/v1/audio-features?ids={ids_str}" # ### URL OFICIAL AQUI ###
            
            r = requests.get(url_feat, headers={'Authorization': f'Bearer {token}'}, timeout=10)
            if r.status_code == 200:
                feats_data = r.json().get('audio_features', [])
                for ft in feats_data:
                    if ft: 
                        features_meta[ft['id']] = {
                            'Valence': ft.get('valence'),
                            'Energy': ft.get('energy'),
                            'Danceability': ft.get('danceability'),
                            'Acousticness': ft.get('acousticness'),
                            'Instrumentalness': ft.get('instrumentalness'),
                            'Tempo': ft.get('tempo')
                        }
        except: pass

    # === 5.3 APLICAÇÃO ===
    for res in new_results:
        aid = res.get('Artist_ID')
        if aid in artist_meta:
            res['Artist_Genres'] = artist_meta[aid]
        
        tid = res.get('Track_ID')
        if tid in features_meta:
            feats = features_meta[tid]
            res.update(feats)

# --- 6. MERGE E LIMPEZA ---
if new_results:
    df_new = pd.DataFrame(new_results)
    if not df_cache.empty:
        df_final_cache = pd.concat([df_cache, df_new], ignore_index=True)
    else:
        df_final_cache = df_new
    
    df_final_cache.drop_duplicates(subset=['Key'], keep='last', inplace=True)
    try: df_final_cache.to_csv(CACHE_FILE, index=False, quoting=csv.QUOTE_ALL)
    except: pass
else:
    df_final_cache = df_cache

# --- 7. OUTPUT ---
if not df_final_cache.empty:
    final_output = pd.merge(df_input, df_final_cache, on='Key', how='left', suffixes=('', '_y'))
    
    final_output['Artist'] = final_output['Official_Artist'].fillna(final_output['Artist'])
    final_output['Track'] = final_output['Official_Track'].fillna(final_output['Track'])
    final_output['Album'] = final_output['Official_Album'].fillna(final_output['Album'])
    
    final_output['Status'] = final_output['Status'].fillna('Not Found')
    final_output['Source'] = final_output['Source'].fillna('Original')

    cols_to_keep = [
        'Artist', 'Track', 'Album',
        'Release_Date', 'Duration_Ms', 'Status', 'Source', 
        'Cover_Image', 'Artist_Genres', 'Track_Popularity', 'Is_Explicit',
        'Artist_ID', 'Track_ID',
        'Valence', 'Energy', 'Danceability', 'Acousticness', 'Instrumentalness', 'Tempo'
    ]
    
    orig_cols = [c for c in df_input.columns if c not in ['Artist_Clean', 'Track_Clean', 'Album_Clean', 'Key', 'Artist', 'Track', 'Album']]
    final_cols = list(set(cols_to_keep + orig_cols))
    
    final_cols = [c for c in final_cols if c in final_output.columns]
    
    final_output = final_output[final_cols]
else:
    final_output = df_input

print(final_output)
