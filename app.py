import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import numpy as np
import requests
import re
import time
import textwrap
import unicodedata
import os
import io
import uuid
from datetime import date
from bs4 import BeautifulSoup
import logging

# ロギング設定（例外握りつぶし対策）
logging.basicConfig(level=logging.WARNING, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# ============================================================
# GitHub自動取得設定
# リポジトリ構成: today/ prev/ train/ wood/
# ============================================================
GITHUB_REPO         = "jiroramone/haichi-app"
GITHUB_BRANCH       = "main"
GITHUB_FOLDER_TODAY = "today"
GITHUB_FOLDER_PREV  = "prev"
GITHUB_FOLDER_TRAIN = "train"
GITHUB_FOLDER_WOOD  = "wood"

def _github_latest_file(folder: str):
    api_url = (f"https://api.github.com/repos/{GITHUB_REPO}"
               f"/contents/{folder}?ref={GITHUB_BRANCH}")
    try:
        r = requests.get(api_url, timeout=10,
                         headers={"Accept": "application/vnd.github+json"})
        if r.status_code != 200:
            return None, None
        files = [f for f in r.json()
                 if isinstance(f, dict) and f.get("type") == "file"
                 and f.get("name", "").lower().endswith(".csv")]
        if not files:
            return None, None
        files.sort(key=lambda x: x["name"], reverse=True)
        latest = files[0]
        res = requests.get(latest["download_url"], timeout=15)
        return (latest["name"], res.content) if res.status_code == 200 else (None, None)
    except Exception as e:
        logger.warning(f"GitHub取得エラー ({folder}): {e}")
        return None, None

@st.cache_data(ttl=3600)
def load_github_data():
    curr_name,  curr_bytes  = _github_latest_file(GITHUB_FOLDER_TODAY)
    prev_name,  prev_bytes  = _github_latest_file(GITHUB_FOLDER_PREV)
    hanro_name, hanro_bytes = _github_latest_file(GITHUB_FOLDER_TRAIN)
    wood_name,  wood_bytes  = _github_latest_file(GITHUB_FOLDER_WOOD)
    return (curr_bytes, prev_bytes, hanro_bytes, wood_bytes,
            curr_name  or "today.csv",
            prev_name  or "prev.csv",
            hanro_name or "train.csv",
            wood_name  or "wood.csv")
st.set_page_config(layout="wide", page_title="配置・能力ハイブリッド馬券検討システム")

# -------------------------------------------------------------------------
# 🎮 ゲーム風アニメーション & スタイリング用 CSS インジェクション
# -------------------------------------------------------------------------
st.html("""
<style>
/* カードコンテナの基本アニメーション */
.game-card-container {
    transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);
    border-radius: 12px !important;
    border: 1px solid #E0E0E0 !important;
    background-color: #FAFAFA;
    box-sizing: border-box;
    white-space: normal !important;
    height: 100%;
    min-width: 0 !important;
    word-break: break-word;
}

/* ホバー時に浮き上がるアニメーション */
.game-card-container:hover {
    transform: translateY(-5px) scale(1.01);
    box-shadow: 0 12px 24px rgba(0,0,0,0.12) !important;
    border-color: #4CAF50 !important;
}

/* 印エフェクト */
.active-honmei { box-shadow: 0 0 12px rgba(255, 87, 34, 0.4) !important; border: 2px solid #FF5722 !important; background-color: #FBE9E7 !important; }
.active-taikou { box-shadow: 0 0 12px rgba(33, 150, 243, 0.4) !important; border: 2px solid #2196F3 !important; background-color: #E3F2FD !important; }
.active-tanana { box-shadow: 0 0 12px rgba(76, 175, 80, 0.4) !important; border: 2px solid #4CAF50 !important; background-color: #E8F5E9 !important; }
.active-renka { box-shadow: 0 0 12px rgba(255, 193, 7, 0.4) !important; border: 2px solid #FFC107 !important; background-color: #FFF8E1 !important; }
.active-hoshi { box-shadow: 0 0 12px rgba(156, 39, 176, 0.4) !important; border: 2px solid #9C27B0 !important; background-color: #F3E5F5 !important; }
.active-keshi { opacity: 0.4 !important; transform: scale(0.98); filter: grayscale(100%); background-color: #ECEFF1 !important; box-shadow: none !important; border: 1px solid #B0BEC5 !important; }

/* スクロールバーのデザイン共通化 */
div[data-testid="stHorizontalBlock"]::-webkit-scrollbar {
    height: 12px;
}
div[data-testid="stHorizontalBlock"]::-webkit-scrollbar-track {
    background: #E0E0E0;
    border-radius: 6px;
}
div[data-testid="stHorizontalBlock"]::-webkit-scrollbar-thumb {
    background: #90A4AE;
    border-radius: 6px;
    border: 2px solid #E0E0E0;
}
div[data-testid="stHorizontalBlock"]::-webkit-scrollbar-thumb:hover {
    background: #607D8B;
}
</style>
""")

# --- 専用メモリの初期化 ---
# 【改修】セッション初期化を辞書で一括管理
_SESSION_DEFAULTS = {
    'saved_chaku': {},
    'ignored_horses': {},
    'user_markers': {},
    'partner_cache': {},
    'fully_processed_df': pd.DataFrame(),
    'cached_owabi_riders': set(),
}
for _k, _v in _SESSION_DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v
st.title("🏇 配置・能力ハイブリッド馬券検討システム")
st.markdown("出馬表の**配置（カラー判定・ペア・対称）**と、**黄金比能力指数・調教ラップ**を統合したスリム版検討システムです。")

JRA_VENUES = {'札幌': '01', '函館': '02', '福島': '03', '新潟': '04', '東京': '05', '中山': '06', '中京': '07', '京都': '08', '阪神': '09', '小倉': '10'}

# -------------------------------------------------------------------------
# ヘルパー関数
# -------------------------------------------------------------------------
def parse_date(val):
    val_str = str(val)
    match = re.search(r'\d+', val_str)
    if match:
        nums = match.group()
        if len(nums) == 6:
            return pd.to_datetime(nums, format='%y%m%d', errors='coerce')
        elif len(nums) >= 8:
            return pd.to_datetime(nums[:8], format='%Y%m%d', errors='coerce')
    return pd.NaT

def clean_horse_name(name):
    if pd.isna(name) or name is None:
        return ""
    name_str = unicodedata.normalize('NFKC', str(name))
    name_str = name_str.strip().replace(" ", "").replace(" ", "").replace("$", "").replace("*", "").replace("＊", "")
    name_str = re.sub(r'[\(（\[［〇○□][外地父抽][\)）\]］]?', '', name_str)
    name_str = re.sub(r'^[〇○□]+', '', name_str)
    return name_str

def normalize_rank(val):
    norm_str = unicodedata.normalize('NFKC', str(val))
    match = re.search(r'\d+', norm_str)
    if match:
        return int(match.group())
    return pd.NA

def clean_time_diff(val):
    if pd.isna(val) or str(val).strip() == "":
        return np.nan
    cleaned = str(val).replace('秒', '').replace('+', '').strip()
    try:
        return float(cleaned)
    except ValueError:
        return np.nan

def format_lap_time(val):
    try:
        return f"{float(val):.1f}"
    except (ValueError, TypeError):
        return "-"

# 【改修】CSVパス候補リスト（集中管理）
MASTER_CSV_CANDIDATES = [
    r"C:\Users\keita\Desktop\配置馬券AC\2023-2026.csv",
    "2023-2026.csv",
    r"C:\Users\keita\Desktop\配置馬券AC\2024-2026タイム付き.csv",
    "2024-2026タイム付き.csv",
    r"C:\Users\keita\Desktop\配置馬券AC\2024-2026pci付き.csv",
    "2025-2026 タイム付き.csv",
]

def _read_csv_with_encoding(filepath_or_buffer):
    """【改修】UTF-8 → CP932 の順でCSV読み込みを試みる共通関数"""
    try:
        return pd.read_csv(filepath_or_buffer, encoding='utf-8')
    except UnicodeDecodeError:
        if hasattr(filepath_or_buffer, 'seek'):
            filepath_or_buffer.seek(0)
        return pd.read_csv(filepath_or_buffer, encoding='cp932')

@st.cache_data
def get_master_history_data():
    """【改修】候補リストから最初に存在するCSVを自動選択"""
    filepath = None
    for candidate in MASTER_CSV_CANDIDATES:
        if os.path.exists(candidate):
            filepath = candidate
            break
    if filepath is None:
        return None
    try:
        df = _read_csv_with_encoding(filepath)
        df['date'] = df['日付'].apply(parse_date)
        if 'レースID(新)' in df.columns:
            df['race_id'] = df['レースID(新)'].astype(str).str.strip().str[:-2]
        else:
            df['race_id'] = df['日付'].astype(str) + df['場所'].astype(str) + df['Ｒ'].astype(str)
        df['rank'] = df['着順'].apply(normalize_rank)
        df['馬名'] = df['馬名'].apply(clean_horse_name)
        df = df.dropna(subset=['date']).sort_values(by=['馬名', 'date']).reset_index(drop=True)
        return df
    except Exception as e:
        logger.warning(f'get_master_history_data: 読み込み失敗 ({filepath}): {e}')
        return None

def get_manual_history_data(file_bytes_content):
    f = io.BytesIO(file_bytes_content)
    try:
        try: 
            df = pd.read_csv(f, encoding='utf-8')
        except UnicodeDecodeError: 
            f.seek(0)
            df = pd.read_csv(f, encoding='cp932')
            
        df['date'] = df['日付'].apply(parse_date)
        if 'レースID(新)' in df.columns: 
            df['race_id'] = df['レースID(新)'].astype(str).str.strip().str[:-2]
        else: 
            df['race_id'] = df['日付'].astype(str) + df['場所'].astype(str) + df['Ｒ'].astype(str)
            
        df['rank'] = df['着順'].apply(normalize_rank)
        df['馬名'] = df['馬名'].apply(clean_horse_name)
        df = df.dropna(subset=['date']).sort_values(by=['馬名', 'date']).reset_index(drop=True)
        return df
    except Exception:
        return None

# -------------------------------------------------------------------------
# 黄金比能力判定
# -------------------------------------------------------------------------
def apply_performance_levels(curr_df, history_df, global_target_datetime):
    if curr_df.empty: 
        return curr_df
        
    time_diff_col = '着差' if history_df is not None and '着差' in history_df.columns else None
    leg_type_col = '脚質' if history_df is not None and '脚質' in history_df.columns else None
    results = []
    
    for idx, row in curr_df.iterrows():
        target_horse = clean_horse_name(row['馬名'])
        
        if '日付S' in curr_df.columns and pd.notna(row['日付S']): 
            target_datetime = pd.to_datetime(row['日付S'], errors='coerce')
        elif '日付' in curr_df.columns and pd.notna(row['日付']): 
            target_datetime = parse_date(row['日付'])
        else: 
            target_datetime = global_target_datetime
            
        if pd.isna(target_datetime) or target_datetime is pd.NaT: 
            target_datetime = global_target_datetime
            
        if history_df is not None:
            horse_history = history_df[(history_df['馬名'] == target_horse) & (history_df['date'] < target_datetime)]
        else:
            horse_history = pd.DataFrame()
        
        res_data = {
            '総合指数': 0.0, 'レベル点': 0.0, '自力点': 0.0, 'ボーナス減点': 0.0,
            '前走着順': np.nan, 'レース間隔': '-', '好走/次走あり': '0/0',
            '前走着差': '-', '前走脚質': '-', '長期休養フラグ': '-', '前走日付': 'データなし'
        }
        
        if not horse_history.empty:
            prev_race = horse_history.iloc[-1]
            prev_race_id = prev_race['race_id']
            prev_race_date = prev_race['date']
            rank_val = prev_race['rank']
            
            if time_diff_col and pd.notna(prev_race[time_diff_col]):
                time_diff_num = clean_time_diff(prev_race[time_diff_col])
            else:
                time_diff_num = None
                
            leg_val = prev_race[leg_type_col] if leg_type_col and pd.notna(prev_race[leg_type_col]) else None
            
            delta_days = (target_datetime - prev_race_date).days
            if delta_days <= 8: 
                interval_str = "連闘"
                interval_weeks = 1
            else: 
                naka_shu = (delta_days - 6) // 7
                interval_str = f"中{naka_shu}週"
                interval_weeks = naka_shu + 1
            
            if history_df is not None:
                rivals = history_df[(history_df['race_id'] == prev_race_id) & (history_df['馬名'] != target_horse)]
            else:
                rivals = pd.DataFrame()
                
            rival_names = rivals['馬名'].unique() if not rivals.empty else []
            field_size = len(rival_names) + 1
            
            # 【高速化】ライバルの次走をまとめてクエリ（forループ → isin+groupbyでベクトル化）
            rival_future_all = history_df[
                history_df['馬名'].isin(rival_names) &
                (history_df['date'] > prev_race_date) &
                (history_df['date'] < target_datetime)
            ] if len(rival_names) > 0 else pd.DataFrame()

            rival_next = (rival_future_all
                          .sort_values('date')
                          .groupby('馬名', sort=False)
                          .first()
                          .reset_index()) if not rival_future_all.empty else pd.DataFrame()

            next_race_found   = len(rival_next)
            rival_top1_count  = int((rival_next['rank'] == 1).sum()) if not rival_next.empty else 0
            rival_top2_count  = int((rival_next['rank'] <= 2).sum()) if not rival_next.empty else 0
            high_achievers    = int((rival_next['rank'] <= 3).sum()) if not rival_next.empty else 0
            rival_top5_count  = int((rival_next['rank'] <= 5).sum()) if not rival_next.empty else 0

            rival_rank_map      = rivals.set_index('馬名')['rank'].to_dict() if not rivals.empty else {}
            top1_names          = rival_next[rival_next['rank'] == 1]['馬名'].tolist() if not rival_next.empty else []
            top3_names          = rival_next[rival_next['rank'] <= 3]['馬名'].tolist() if not rival_next.empty else []
            top5_names          = rival_next[rival_next['rank'] <= 5]['馬名'].tolist() if not rival_next.empty else []
            top1_achiever_ranks = sorted([int(rival_rank_map[n]) for n in top1_names if n in rival_rank_map and pd.notna(rival_rank_map[n])])
            high_achiever_ranks = sorted([int(rival_rank_map[n]) for n in top3_names if n in rival_rank_map and pd.notna(rival_rank_map[n])])
            top5_achiever_ranks = sorted([int(rival_rank_map[n]) for n in top5_names if n in rival_rank_map and pd.notna(rival_rank_map[n])])
            
            if top1_achiever_ranks:
                top1_text = f"🥇1着:{rival_top1_count}頭(前{','.join(map(str, sorted(top1_achiever_ranks)))}着)"
            else:
                top1_text = f"🥇1着:{rival_top1_count}頭"

            if high_achiever_ranks:
                top3_text = f"🥉3着内:{high_achievers}頭(前{','.join(map(str, sorted(high_achiever_ranks)))}着)"
            else:
                top3_text = f"🥉3着内:{high_achievers}頭"

            top5_text = f"🎖️5着内:{rival_top5_count}頭"

            hoso_text = f"【次走: {next_race_found}頭】 {top1_text} ｜ {top3_text} ｜ {top5_text}"

            flag_status = "-"
            try:
                age_val = None
                for c in ['年齢', '性齢', '馬齢', '齢']:
                    if c in row.index and pd.notna(row[c]) and str(row[c]).strip():
                        age_val = row[c]
                        break
                if not age_val:
                    for c in ['年齢', '性齢', '馬齢', '齢']:
                        if c in prev_race.index and pd.notna(prev_race[c]) and str(prev_race[c]).strip():
                            age_val = prev_race[c]
                            break
                            
                horse_age = 99
                if age_val:
                    age_match = re.search(r'\d+', str(age_val))
                    if age_match:
                        horse_age = int(age_match.group())
                
                race_class_parts = []
                for c in ['クラス名', 'クラス', '条件', '競走名', 'レース名']:
                    if c in row.index and pd.notna(row[c]):
                        race_class_parts.append(str(row[c]))
                
                race_class_str = unicodedata.normalize('NFKC', " ".join(race_class_parts))
                
                if not race_class_str.strip() or race_class_str.strip() in ['nan', 'None']: 
                    is_target_class = True
                else: 
                    is_target_class = any(c in race_class_str for c in ['未勝利', '1勝', '2勝', '3勝'])
                
                if delta_days >= 83 and (horse_age <= 5 or horse_age == 99) and is_target_class:
                    flag_status = f"休養(次走{next_race_found}頭中 ➡ 🥇1着:{rival_top1_count}頭 / 🥈連対:{rival_top2_count}頭 / 🎖️5着内:{rival_top5_count}頭)"
                    if rival_top5_count >= 8 and rival_top2_count >= 1: 
                        flag_status = f"🚩 注目(超Hレベル休養明け・次走{next_race_found}頭中 ➡ 🥇1着:{rival_top1_count}頭 / 🥈連対:{rival_top2_count}頭)"
            except Exception: 
                pass
            
            if next_race_found > 0:
                bayesian_rate = (high_achievers + 0.6) / (next_race_found + 3.0)
            else:
                bayesian_rate = (0.6 / 3.0)
                
            raw_score_a = min(50.0, bayesian_rate * 125) 
            
            if interval_weeks <= 8:
                decay_rate = 1.0
            else:
                decay_rate = max(0.4, 1.0 - ((interval_weeks - 8) * 0.025))
                
            if pd.notna(rank_val):
                rank_int = int(rank_val)
            else:
                rank_int = 99
            
            if rank_int <= 5:
                rank_discount = 1.0
            elif rank_int <= 9:
                rank_discount = 0.8
            else:
                rank_discount = 0.5
                
            score_a = raw_score_a * decay_rate * rank_discount
            
            if pd.notna(time_diff_num): 
                base_score_b = max(0.0, 50.0 - (time_diff_num * 20.0))
            else: 
                if rank_int == 1: base_score_b = 50.0
                elif rank_int == 2: base_score_b = 35.0
                elif rank_int == 3: base_score_b = 25.0
                elif rank_int <= 5: base_score_b = 15.0
                elif rank_int <= 9: base_score_b = 5.0
                else: base_score_b = 0.0
            
            rivals_count_safe = max(1, len(rival_names))
            reliability_ratio = min(1.0, next_race_found / rivals_count_safe)
            weight_multiplier = 1.0 + ((1.0 - reliability_ratio) * 0.5) 
            score_b = base_score_b * weight_multiplier
            
            score_c = max(0, field_size - 10) * 1.0
            
            if interval_str == "連闘" or interval_weeks >= 25:
                score_d = -10.0
            else:
                score_d = 0.0
            
            if pd.notna(leg_val):
                leg_str = str(leg_val).strip()
            else:
                leg_str = ""
                
            if "逃" in leg_str or "先" in leg_str or "1" in leg_str.split('-')[:2]: 
                score_c += 3.0
                
            lap_eval = row.get('ラップ評価', '-')
            if pd.notna(lap_eval):
                if '🌟激アツ: 5' in lap_eval: score_c += 8.0  
                elif '5 終い11秒台の加速' in lap_eval: score_c += 5.0
                elif '3 終い2F12秒台' in lap_eval: score_c += 3.0
                elif '1 終いのみ12秒台' in lap_eval: score_c += 1.0
                elif '終い2F11秒台まとめの減速' in lap_eval: score_c += 4.0  
                elif '🌟激アツ: 6' in lap_eval: score_c += 5.0  
                elif '6 2Fのみ11秒台の減速' in lap_eval: score_c += 2.0  
                elif '🚨危険(地雷)' in lap_eval: score_c -= 5.0  
                
            total_index = round(score_a + score_b + score_c + score_d, 1)
            
            if pd.notna(prev_race[time_diff_col]):
                prev_diff_str = f"{prev_race[time_diff_col]}秒"
            else:
                prev_diff_str = "-"
            
            res_data.update({
                '総合指数': total_index, 
                'レース間隔': interval_str, 
                '前走着順': rank_int if rank_int != 99 else None,
                '前走着差': prev_diff_str, 
                '前走脚質': leg_str if leg_str else "-",
                'レベル点': round(score_a, 1), 
                '自力点': round(score_b, 1), 
                'ボーナス減点': round(score_c + score_d, 1),
                '長期休養フラグ': flag_status, 
                '好走/次走あり': hoso_text, 
                '前走日付': prev_race_date.strftime('%Y-%m-%d')
            })
            
        results.append(res_data)
        
    res_df = pd.DataFrame(results)
    for col in res_df.columns: 
        curr_df[col] = res_df[col].values
        
    return curr_df

def escape_html(text: str) -> str:
    """【改修】HTMLインジェクション対策：特殊文字をエスケープ"""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))

def get_waku_info(umaban, tousu):
    if pd.isna(tousu) or tousu <= 0: 
        return 1, "#FFFFFF", "#000000", "#CCCCCC"
        
    tousu = int(tousu)
    umaban = int(umaban)
    
    if tousu <= 8: 
        waku = umaban
    else:
        base = tousu // 8
        rem = tousu % 8
        waku_sizes = [base] * 8
        for i in range(rem): 
            waku_sizes[7 - i] += 1
            
        cumulative = 0
        waku = 8
        for w_idx, size in enumerate(waku_sizes):
            cumulative += size
            if umaban <= cumulative: 
                waku = w_idx + 1
                break
                
    w_c = {
        1: ("#FFFFFF", "#000000", "#CCCCCC"), 
        2: ("#1A1A1A", "#FFFFFF", "#1A1A1A"), 
        3: ("#E53935", "#FFFFFF", "#E53935"), 
        4: ("#1E88E5", "#FFFFFF", "#1E88E5"), 
        5: ("#FDD835", "#000000", "#FDD835"), 
        6: ("#43A047", "#FFFFFF", "#43A047"), 
        7: ("#FB8C00", "#000000", "#FB8C00"), 
        8: ("#F06292", "#FFFFFF", "#F06292")
    }
    bg, fg, border = w_c.get(waku, ("#FFFFFF", "#000000", "#CCCCCC"))
    return waku, bg, fg, border

def has_previous_pair_race(pair_str, current_r_num, my_venue):
    if not pair_str or pd.isna(pair_str) or str(pair_str).strip() in ["", "ー", "nan"]: 
        return False
        
    items = [x.strip() for x in str(pair_str).split(',') if x.strip()]
    for item in items:
        m = re.match(r'^([^\s\(\)]+)(?:\(([^)]+)\))?$', item)
        if m:
            race_key = m.group(1)
            m_r = re.search(r'(\d+)R', race_key)
            if m_r:
                r_val = int(m_r.group(1))
                venue_part = re.sub(r'\d+R', '', race_key)
                is_same_venue = (not venue_part) or (venue_part == my_venue)
                if is_same_venue and r_val < current_r_num: 
                    return True
        else:
            m_r = re.search(r'(\d+)R', item)
            if m_r:
                r_val = int(m_r.group(1))
                venue_part = re.sub(r'\d+R', '', item)
                is_same_venue = (not venue_part) or (venue_part == my_venue)
                if is_same_venue and r_val < current_r_num: 
                    return True
    return False

def split_by_comma_outside_parentheses(text):
    if not text: 
        return []
    items = []
    current = []
    depth = 0
    for char in text:
        if char == '(': 
            depth += 1
        elif char == ')': 
            depth -= 1
            
        if char == ',' and depth == 0: 
            items.append("".join(current).strip())
            current = []
        else: 
            current.append(char)
            
    if current: 
        items.append("".join(current).strip())
    return [x for x in items if x]

def format_condensed_pairs(pair_str):
    if not pair_str or pd.isna(pair_str) or str(pair_str).strip() in ["", "ー", "nan"]: 
        return ""
        
    race_map = {}
    for item in [x.strip() for x in str(pair_str).split(',') if x.strip()]:
        m = re.match(r'^([^\s\(\)]+)(?:\(([^)]+)\))?$', item)
        if m:
            race_key = m.group(1)
            sig_part = m.group(2)
            if race_key not in race_map: 
                race_map[race_key] = set()
            if sig_part:
                for s in sig_part.split(','):
                    if s.strip(): 
                        race_map[race_key].add(s.strip())
        else:
            if item not in race_map: 
                race_map[item] = set()
                
    def sort_key(x):
        race_name = x[0]
        venue = re.sub(r'\d+R', '', race_name)
        m_r = re.search(r'(\d+)R', race_name)
        r_num = int(m_r.group(1)) if m_r else 99
        return (venue, r_num)
        
    condensed_items = []
    for k, v in sorted(race_map.items(), key=sort_key):
        if v:
            condensed_items.append(f"{k}({','.join(sorted(list(v)))})")
        else:
            condensed_items.append(k)
            
    return ",".join(condensed_items)

def make_badge_html(label_type, text):
    if pd.isna(text) or text is None or str(text).strip() in ["", "ー"]: 
        return ""
        
    text_str = str(text).strip()
    bg = "#f0f0f0"
    color = "#333333"
    
    if "〇" in text_str: 
        bg = "#FFD2D2"
        color = "#800000"
    elif "▲" in text_str: 
        bg = "#FFE4C4"
        color = "#8B4513"
    elif "△" in text_str: 
        bg = "#FFF9C4"
        color = "#6D4C41"
    elif "◆" in text_str: 
        bg = "#D6E4FF"
        color = "#002D80"
    elif "✖" in text_str: 
        bg = "#ECEFF1"
        color = "#37474F"
    elif "✨🟦" in text_str: 
        bg = "#E3F2FD"
        color = "#0D47A1"
    elif "🔄" in text_str: 
        bg = "#F3E5F5"
        color = "#4A148C"
    
    if label_type == "騎手":
        icon = "👤"
    elif label_type == "調教":
        icon = "🏠"
    elif label_type == "馬主":
        icon = "👑"
    else:
        icon = "🔖"
        
    return f"""
    <div style="background-color: {bg}; color: {color}; font-size: 13px; font-weight: bold; padding: 6px 10px; border-radius: 6px; margin-top: 4px; display: flex; align-items: center; gap: 8px; border: 1px solid {bg};">
        <span>{icon}</span><span>{text_str}</span>
    </div>
    """

def scrape_yahoo_odds_jra(date_str, venue_name, r_num):
    cache_key = f"yahoo_base_id_{date_str}_{venue_name}"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    if cache_key not in st.session_state:
        parts = str(date_str).replace('-', '/').replace('.', '/').split(' ')[0].split('/')
        if len(parts) < 3: 
            return None
            
        year = parts[0]
        month = int(parts[1])
        target_day = int(parts[2])
        
        try:
            url = f"https://sports.yahoo.co.jp/keiba/schedule/monthly/?year={year}&month={month}"
            res_sched = requests.get(url, headers=headers, timeout=5)
            if res_sched.status_code == 200:
                soup = BeautifulSoup(res_sched.text, 'html.parser')
                best_id = None
                min_diff = 999
                
                for tr in soup.find_all('tr'):
                    current_day = None
                    for td in tr.find_all('td'):
                        m_day = re.search(r'(\d+)日', td.get_text().strip())
                        if m_day: 
                            current_day = int(m_day.group(1))
                            break
                            
                    link = tr.find('a', href=re.compile(r'/keiba/race/list/\d{8}'))
                    if link and venue_name in link.get_text():
                        m_id = re.search(r'/keiba/race/list/(\d{8})', link['href'])
                        if m_id and current_day is not None:
                            diff = abs(current_day - target_day)
                            if diff < min_diff:
                                min_diff = diff
                                best_id = m_id.group(1)
                                
                if best_id: 
                    st.session_state[cache_key] = best_id
        except Exception: 
            pass

    base_id = st.session_state.get(cache_key)
    if not base_id: 
        return None
        
    race_id = f"{base_id}{r_num:02d}"
    urls = [
        f"https://sports.yahoo.co.jp/keiba/race/odds/tf/{race_id}",
        f"https://sports.yahoo.co.jp/keiba/race/denma/{race_id}"
    ]
    
    for url in urls:
        try:
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                odds_map = {}
                for table in soup.find_all('table'):
                    headers_text = [th.get_text().strip() for th in table.find_all('th')]
                    if '馬番' in headers_text and any('オッズ' in h or '単勝' in h for h in headers_text):
                        u_idx = headers_text.index('馬番')
                        o_idx = next(i for i, h in enumerate(headers_text) if 'オッズ' in h or '単勝' in h)
                        
                        tbody = table.find('tbody')
                        rows = tbody.find_all('tr') if tbody else table.find_all('tr')
                        
                        for row in rows:
                            tds = row.find_all('td')
                            if len(tds) > max(u_idx, o_idx):
                                num_txt = tds[u_idx].get_text().strip()
                                span = tds[o_idx].find('span')
                                o_txt = span.get_text().strip() if span else tds[o_idx].get_text().strip()
                                
                                m = re.search(r'\((.*?)\)', o_txt)
                                if m: 
                                    o_txt = m.group(1)
                                    
                                if num_txt.isdigit() and o_txt.replace('.', '', 1).isdigit(): 
                                    odds_map[int(num_txt)] = float(o_txt)
                                    
                        if odds_map: 
                            return odds_map
        except Exception: 
            continue
            
    return None

def preprocess_and_calculate_haichi(df):
    if df.empty: 
        return df
        
    df = df.reset_index(drop=True)
    
    if 'Ｒ' in df.columns: 
        df['Ｒ'] = df['Ｒ'].astype(str).str.extract(r'(\d+)')[0].astype(int)
        
    if '場所' in df.columns: 
        df['場所'] = df['場所'].astype(str).str.extract(r'([^\d\s\(\)]+)')[0].str.strip()
        
    for col in ['騎手', '調教師', '馬主(最新/仮想)']:
        if col in df.columns: 
            df[col] = df[col].apply(clean_horse_name)
            
    df['頭数'] = df.groupby(['日付S', '場所', 'Ｒ'])['馬番'].transform('max')
    df['正番'] = df['馬番'].astype(int)
    df['逆番'] = df['頭数'].astype(int) - df['正番'] + 1
    df['正循環'] = df['正番'] + df['頭数'].astype(int)
    df['逆循環'] = df['逆番'] + df['頭数'].astype(int)
    
    return df.sort_values(by=['日付S', '場所', 'Ｒ', '馬番']).reset_index(drop=True)

def calculate_haichi_features(df):
    df = df.copy()
    if df.empty: 
        return df
        
    df['is_blue_jockey'] = df['騎手_青塗'].astype(float)
    df['next_to_blue'] = 0.0
    df['is_symmetry'] = 0.0
    df['next_to_symmetry'] = 0.0
    
    for (date, loc, r), group in df.groupby(['日付S', '場所', 'Ｒ']):
        blue_nums = group[group['is_blue_jockey'] == 1.0]['馬番'].tolist()
        for b_num in blue_nums:
            target = (df['日付S']==date) & (df['場所']==loc) & (df['Ｒ']==r) & ((df['馬番']==b_num+1)|(df['馬番']==b_num-1))
            df.loc[target, 'next_to_blue'] = 1.0
            
        for t_col in ['調教師']:
            if t_col not in df.columns: 
                continue
                
            for name, t_group in group.groupby(t_col):
                name_str = str(name).strip()
                if not name_str or name_str in ["", "ー", "nan", "None", "不明"]: 
                    continue
                    
                if len(t_group) > 1:
                    for idx, row in t_group.iterrows():
                        other_gyaku = t_group[t_group['馬番'] != row['馬番']]['逆番'].tolist()
                        if row['正番'] in other_gyaku:
                            target_base = (df['日付S']==date) & (df['場所']==loc) & (df['Ｒ']==r)
                            df.loc[target_base & (df['馬番']==row['馬番']), 'is_symmetry'] = 1.0
                            
                            target_next = target_base & ((df['馬番']==row['馬番']+1)|(df['馬番']==row['馬番']-1)) & (df['is_symmetry']==0)
                            df.loc[target_next, 'next_to_symmetry'] = 1.0
                            
    return df

def judge_yellow_and_pairs(combined_df, target_col='騎手'):
    combined_df[f'{target_col}_黄塗'] = False
    combined_df[f'{target_col}_ペア'] = ""
    combined_df[f'{target_col}_厩舎同ペア'] = False
    
    if combined_df.empty or target_col not in combined_df.columns: 
        return combined_df

    pair_map = {
        ('正番', '正番'): 'A', ('正番', '逆番'): 'B', ('正番', '正循環'): 'C', ('正番', '逆循環'): 'D', 
        ('逆番', '正番'): 'E', ('逆番', '逆番'): 'F', ('逆番', '正循環'): 'G', ('逆番', '逆循環'): 'H', 
        ('正循環', '正番'): 'I', ('正循環', '逆番'): 'J', ('正循環', '正循環'): 'K', ('正循環', '逆循環'): 'L', 
        ('逆循環', '正番'): 'M', ('逆循環', '逆番'): 'N', ('逆循環', '正循環'): 'O', ('逆循環', '逆循環'): 'P'
    }

    for (date, loc), loc_group in combined_df.groupby(['日付S', '場所']):
        for name, group in loc_group.groupby(target_col):
            name_str = str(name).strip()
            if not name_str or name_str in ["", "ー", "nan", "None", "不明"]: 
                continue
            
            race_nums = sorted(group['Ｒ'].unique())
            if len(race_nums) < 2:
                continue
                
            for i in range(len(race_nums) - 1):
                prev_r = race_nums[i]
                curr_r = race_nums[i + 1]
                
                prev_rows = group[group['Ｒ'] == prev_r]
                curr_rows = group[group['Ｒ'] == curr_r]
                
                for idx_prev, prev_row in prev_rows.iterrows():
                    for idx_curr, curr_row in curr_rows.iterrows():
                        match_found = False
                        detected_pairs_curr = []
                        detected_pairs_prev = []
                        
                        info_for_curr = f"{prev_r}R"
                        info_for_prev = f"{curr_r}R"
                        
                        is_stable_match = False
                        if '騎手' in prev_row and '調教師' in prev_row:
                            if prev_row['騎手'] == curr_row['騎手'] and prev_row['調教師'] == curr_row['調教師']:
                                is_stable_match = True
                        
                        for p1 in ['正番', '逆番', '正循環', '逆循環']:
                            for p2 in ['正番', '逆番', '正循環', '逆循環']:
                                if prev_row[p1] == curr_row[p2]:
                                    match_found = True
                                    sig = pair_map[(p1, p2)]
                                    detected_pairs_curr.append(f"{info_for_curr}({sig})")
                                    detected_pairs_prev.append(f"{info_for_prev}({sig})")
                        
                        p_prev = prev_row['正番']
                        p_curr = curr_row['正番']
                        if (p_prev % 10 == p_curr % 10) and (p_prev != p_curr):
                            match_found = True
                            sig = 'Q' if p_prev < p_curr else 'R'
                            detected_pairs_curr.append(f"{info_for_curr}({sig})")
                            detected_pairs_prev.append(f"{info_for_prev}({sig})")
                        
                        if match_found:
                            combined_df.at[idx_curr, f'{target_col}_黄塗'] = True
                            combined_df.at[idx_prev, f'{target_col}_黄塗'] = True
                            
                            existing_curr = combined_df.at[idx_curr, f'{target_col}_ペア']
                            ex_list_curr = [x.strip() for x in existing_curr.split(',')] if existing_curr else []
                            for item in set(detected_pairs_curr):
                                if item not in ex_list_curr:
                                    ex_list_curr.append(item)
                            combined_df.at[idx_curr, f'{target_col}_ペア'] = ",".join(ex_list_curr)
                            
                            existing_prev = combined_df.at[idx_prev, f'{target_col}_ペア']
                            ex_list_prev = [x.strip() for x in existing_prev.split(',')] if existing_prev else []
                            for item in set(detected_pairs_prev):
                                if item not in ex_list_prev:
                                    ex_list_prev.append(item)
                            combined_df.at[idx_prev, f'{target_col}_ペア'] = ",".join(ex_list_prev)
                            
                            if is_stable_match: 
                                combined_df.at[idx_curr, f'{target_col}_厩舎同ペア'] = True
                                combined_df.at[idx_prev, f'{target_col}_厩舎同ペア'] = True

    return combined_df

def judge_blue_coating(df, target_col='騎手'):
    df[f'{target_col}_青塗'] = False
    blue_names = set()
    
    if df.empty or target_col not in df.columns: 
        return df, blue_names
        
    for (date, loc), loc_group in df.groupby(['日付S', '場所']):
        for name, group in loc_group.groupby(target_col):
            name_str = str(name).strip()
            if not name_str or name_str in ["", "ー", "nan", "None", "不明"]: 
                continue
            
            if group['Ｒ'].nunique() >= 2:
                for p in ['正番', '逆番', '正循環', '逆循環']:
                    if group[p].nunique() == 1:
                        df.loc[group.index, f'{target_col}_青塗'] = True
                        blue_names.add((date, name))
                        break 
                        
    return df, blue_names

def extract_owabi_riders(prev_df):
    owabi_riders = set()
    if prev_df.empty or '着順' not in prev_df.columns: 
        return owabi_riders
        
    prev_df, blue_date_riders = judge_blue_coating(prev_df, target_col='騎手')
    
    for date, rider in blue_date_riders:
        rider_races = prev_df[(prev_df['日付S'] == date) & (prev_df['騎手'] == rider)]
        has_hit = False
        for chaku in rider_races['着順']:
            try:
                chaku_int = int(float(str(chaku).strip()))
                if chaku_int in [1, 2, 3]: 
                    has_hit = True
                    break
            except Exception: 
                continue
                
        if not has_hit: 
            owabi_riders.add(rider)
            
    return owabi_riders

def calculate_placement_points(row):
    pts = 0.0
    if row.get('騎手_青塗') or row.get('調教師_青塗') or row.get('馬主(最新/仮想)_青塗', False): 
        pts += 3.0
    elif row.get('騎手_黄塗') or row.get('調教師_黄塗') or row.get('馬主(最新/仮想)_黄塗', False): 
        pts += 1.0
        
    if row.get('騎手_厩舎同ペア') or row.get('調教師_厩舎同ペア'): 
        pts += 2.0
        
    pair_str = str(row.get('騎手_ペア', '')) + str(row.get('調教師_ペア', '')) + str(row.get('馬主(最新/仮想)_ペア', ''))
    if any(p in pair_str for p in ['(C)', '(D)', '(G)', '(H)']): 
        pts += 1.5
        
    if row.get('is_symmetry', 0.0) == 1.0: 
        pts += 1.5
    elif row.get('next_to_symmetry', 0.0) == 1.0: 
        pts += 0.5
        
    odds_val = row.get('temp_odds', 0.0)
    if 10.0 <= odds_val < 20.0: 
        pts += 1.5
    elif 1.0 < odds_val < 50.0: 
        pts += 0.5
    elif odds_val >= 100.0: 
        pts -= 1.0
        
    return round(pts, 1)

def find_all_pair_partners_detailed(row, full_df):
    r_num = int(row['Ｒ'])
    my_venue = row['場所']
    date = row['日付S']
    partners_info = []
    targets = []
    
    if pd.notnull(row.get('騎手')): 
        targets.append(('騎手', row.get('騎手'), '騎手_ペア', 0))
    if pd.notnull(row.get('調教師')): 
        targets.append(('調教師', row.get('調教師'), '調教師_ペア', 1))
    if '馬主(最新/仮想)' in row.index and pd.notnull(row.get('馬主(最新/仮想)')): 
        targets.append(('馬主(最新/仮想)', row.get('馬主(最新/仮想)', 'ー'), '馬主(最新/仮想)_ペア', 2))
        
    for col_name, val, pair_col, cat_idx in targets:
        val_str = str(val).strip()
        if not val_str or val_str in ["", "ー", "nan", "None", "不明"]: 
            continue
            
        pair_text = str(row.get(pair_col, ''))
        if not pair_text or pair_text == "nan" or pair_text == "ー": 
            continue
            
        for item in split_by_comma_outside_parentheses(pair_text):
            m = re.match(r'^([^\s\(\)]+)(?:\(([^)]+)\))?$', item)
            if m:
                race_key = m.group(1)
                pair_sig = f" ({m.group(2)})" if m.group(2) else ""
                m_r = re.search(r'(\d+)R', race_key)
                if m_r:
                    tgt_r_int = int(m_r.group(1))
                    tgt_venue_part = re.sub(r'\d+R', '', race_key)
                    tgt_venue = tgt_venue_part if tgt_venue_part else my_venue
                    
                    match_rows = full_df[
                        (full_df['日付S'] == date) & 
                        (full_df['場所'] == tgt_venue) & 
                        (full_df['Ｒ'] == tgt_r_int) & 
                        (full_df[col_name] == val) & 
                        ~((full_df['場所'] == my_venue) & (full_df['Ｒ'] == r_num) & (full_df['馬番'] == row['馬番']))
                    ]
                
                    for _, m_row in match_rows.iterrows():
                        m_odds = m_row.get('オッズ')
                        m_pop = m_row.get('人気')
                        m_num = int(m_row.get('馬番'))
                        m_name = m_row.get('馬name', m_row.get('馬名', '不明'))
                        
                        c1 = st.session_state['saved_chaku'].get(f"c1_{tgt_venue}_{tgt_r_int}")
                        c2 = st.session_state['saved_chaku'].get(f"c2_{tgt_venue}_{tgt_r_int}")
                        c3 = st.session_state['saved_chaku'].get(f"c3_{tgt_venue}_{tgt_r_int}")
                        
                        chaku_status = "未確定"
                        if c1 is not None or c2 is not None or c3 is not None:
                            if m_num == c1: 
                                chaku_status = "<span style='color:#FFD700; font-weight:bold;'>🏆 1着好走</span>"
                            elif m_num == c2: 
                                chaku_status = "<span style='color:#C0C0C0; font-weight:bold;'>🥈 2着好走</span>"
                            elif m_num == c3: 
                                chaku_status = "<span style='color:#CD7F32; font-weight:bold;'>🥉 3着好走</span>"
                            else: 
                                chaku_status = "<span style='color:#888;'>凡走(4着以下)</span>"
                        
                        odds_txt = f"単勝 {m_odds}倍" if pd.notnull(m_odds) and str(m_odds).strip() not in ["", "nan"] else "単勝 未取得"
                        pop_txt = f"{int(m_pop)}人気" if pd.notnull(m_pop) and str(m_pop).strip() not in ["", "nan"] else "未設定"
                        
                        if col_name == "騎手":
                            category_label = "騎手"
                        elif col_name == "調教師":
                            category_label = "調教"
                        else:
                            category_label = "馬主"
                            
                        venue_label = f"{tgt_venue}" if tgt_venue != my_venue else ""
                        formatted_str = f"🔗 {venue_label}{tgt_r_int}R {m_num}番 {m_name} ({category_label}{pair_sig}) 【{odds_txt} / {pop_txt} / {chaku_status}】"
                        
                        partners_info.append((cat_idx, tgt_venue, tgt_r_int, m_num, formatted_str))
                    
    unique_partners = list(set(partners_info))
    unique_partners.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
    
    result_list = []
    for item in unique_partners:
        result_list.append(item[4])
        
    return result_list

# -------------------------------------------------------------------------
# 🌟 カード描画用の共通関数 (1頭分)
# -------------------------------------------------------------------------
def render_single_horse_card(row_data, selected_venue, curr_df):
    r_num = int(row_data['Ｒ'])
    num_val = int(row_data['馬番']) if pd.notnull(row_data['馬番']) else 0
    name_val = row_data.get('馬name', row_data.get('馬名', '不明'))
    tousu_val = row_data.get('頭数', 16)
    w_num, waku_bg, waku_fg, waku_border = get_waku_info(num_val, tousu_val)
    
    raw_points = row_data.get('配置ポイント', 0.0)
    star_count = min(5, max(1, int(raw_points // 1.5))) if raw_points > 0 else 0
    stars = "★" * star_count if star_count > 0 else ""
    
    horse_key = f"{selected_venue}_{r_num}_{num_val}"
    marker_val = st.session_state['user_markers'].get(horse_key, "未設定")
    
    card_class = "game-card-container"
    if marker_val == "◎": 
        card_class += " active-honmei"
    elif marker_val == "○": 
        card_class += " active-taikou"
    elif marker_val == "▲": 
        card_class += " active-tanana"
    elif marker_val == "△": 
        card_class += " active-renka"
    elif marker_val == "☆": 
        card_class += " active-hoshi"
    elif marker_val == "✖": 
        card_class += " active-keshi"
    
    if marker_val == "未設定" and raw_points > 0: 
        border_left_style = "5px solid #4CAF50"
    else: 
        border_left_style = "1px solid #E0E0E0" if marker_val == "未設定" else "none"
    
    text_color_style = "color: #111111;"
    badge_opacity = "opacity: 1.0;"
    points_style = "color: #FF9800;"
    stars_txt = f" ({stars})" if stars else ""
    
    if marker_val == "✖": 
        text_color_style = "color: #888888;"
        badge_opacity = "opacity: 0.35; filter: grayscale(100%);"
        points_style = "color: #90A4AE;"
        stars_txt = ""
    
    o_val = row_data.get('オッズ', None)
    p_val = row_data.get('人気', None)
    try:
        o_f = float(o_val)
        odds_txt = f"{o_f}倍" if o_f > 0 else "未取得"
    except (ValueError, TypeError):
        odds_txt = "未取得"
    try:
        p_f = float(p_val)
        pop_txt = f"{int(p_f)}人気" if p_f > 0 else "未設定"
    except (ValueError, TypeError):
        pop_txt = "未設定"
    
    jockey_name = row_data.get('騎手', 'ー')
    j_pair = row_data.get('騎手_ペア', '')
    jockey_pair_txt = f"(ペア: {j_pair})" if pd.notnull(j_pair) and j_pair != "" else ""
    
    stable_name = row_data.get('調教師', 'ー')
    t_pair = row_data.get('調教師_ペア', '')
    stable_pair_txt = f"(ペア: {t_pair})" if pd.notnull(t_pair) and t_pair != "" else ""
    
    owner_name = row_data.get('馬主(最新/仮想)', 'ー') if '馬主(最新/仮想)' in row_data.index else 'ー'
    o_pair = row_data.get('馬主(最新/仮想)_ペア', '') if '馬主(最新/仮想)_ペア' in row_data.index else ''
    owner_pair_txt = f"(ペア: {o_pair})" if pd.notnull(o_pair) and o_pair != "" else ""
    
    owner_section_html = ""
    if pd.notnull(owner_name) and owner_name != "ー":
        owner_section_html = f"<div>👑 馬主: <strong style='font-size:14px; color:#111;'>{owner_name}</strong> <span style='color: #4CAF50; font-size: 11px; font-weight:bold; margin-left:4px;'>{owner_pair_txt}</span></div>"

    haichi_elements_html = f"""
    <div style="font-size: 13px; color: #333333; background-color: #FAFAFA; padding: 8px 12px; border-radius: 8px; margin-bottom: 8px; line-height: 1.5; border: 1px solid #ECEFF1; {badge_opacity}">
        <div style="margin-bottom: 2px;">👤 騎手: <strong style="font-size:14px; color:#111;">{jockey_name}</strong> <span style="color:#D500F9; font-size: 11px; font-weight:bold; margin-left:4px;">{jockey_pair_txt}</span></div>
        <div style="margin-bottom: 2px;">🏠 厩舎: <strong style="font-size:14px; color:#111;">{stable_name}</strong> <span style="color:#00B0FF; font-size: 11px; font-weight:bold; margin-left:4px;">{stable_pair_txt}</span></div>
        {owner_section_html}
    </div>"""
    
    perf_score = row_data.get('総合指数', 0.0)
    perf_rank = row_data.get('前走着順')
    perf_rank_txt = f"{int(perf_rank)}着" if (pd.notnull(perf_rank) and not pd.isna(perf_rank)) else "-"
    perf_interval = row_data.get('レース間隔', '-')
    perf_level = row_data.get('レベル点', 0.0)
    perf_jiri = row_data.get('自力点', 0.0)
    perf_bonus = row_data.get('ボーナス減点', 0.0)
    perf_hoso = row_data.get('好走/次走あり', '-')
    perf_diff = row_data.get('前走着差', '-')
    perf_leg = row_data.get('前走脚質', '-')
    perf_kyuyo = row_data.get('長期休養フラグ', '-')
    
    kyuyo_html = ""
    if "🚩" in str(perf_kyuyo): 
        kyuyo_html = f"""<div style="background-color: #E8F5E9; color: #2E7D32; font-size: 11px; font-weight: bold; padding: 4px; border-radius: 4px; margin-top: 4px; border: 1px solid #A5D6A7;">{perf_kyuyo}</div>"""
    elif perf_kyuyo != "-": 
        kyuyo_html = f"""<div style="background-color: #F5F5F5; color: #616161; font-size: 11px; padding: 4px; border-radius: 4px; margin-top: 4px; border: 1px solid #E0E0E0;">{perf_kyuyo}</div>"""

    perf_section_html = f"""
    <div style="margin-top: 8px; margin-bottom: 8px; padding: 8px 12px; background-color: #FFFDE7; border: 1px solid #FFF59D; border-radius: 8px; {badge_opacity}">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
            <span style="font-weight: bold; font-size: 13px; color: #F57F17; display: flex; align-items: center; gap: 4px;"><span>⚡</span> <span>黄金比指数:</span></span>
            <span style="font-weight: bold; font-size: 14px; color: #E65100;">{perf_score if pd.notnull(perf_score) else 0.0} 点</span>
        </div>
        <div style="font-size: 11.5px; color: #5D4037; display: flex; flex-wrap: wrap; row-gap: 2px; column-gap: 8px;">
            <span>前走: <strong>{perf_rank_txt}</strong> ({perf_diff})</span>
            <span>間隔: <strong>{perf_interval}</strong></span>
            <span>脚質: <strong>{perf_leg}</strong></span>
            <span>相手: <strong>{perf_level if pd.notnull(perf_level) else 0.0}点</strong></span>
            <span>自力: <strong>{perf_jiri if pd.notnull(perf_jiri) else 0.0}点</strong></span>
            <span>加減: <strong>{perf_bonus if pd.notnull(perf_bonus) else 0.0}点</strong></span>
            <div style="width: 100%; border-top: 1px dashed #CCC; margin-top: 4px; padding-top: 4px; font-size: 10.5px;">
                {perf_hoso}
            </div>
        </div>
        {kyuyo_html}
    </div>"""
    
    t4 = format_lap_time(row_data.get('4Fタイム', np.nan))
    l4 = format_lap_time(row_data.get('Lap4', np.nan))
    l3 = format_lap_time(row_data.get('Lap3', np.nan))
    l2 = format_lap_time(row_data.get('Lap2', np.nan))
    l1 = format_lap_time(row_data.get('ラスト1F', np.nan))
    perf_lapeval = row_data.get('ラップ評価', '-')

    hanro_bg = "#ECEFF1"
    hanro_color = "#37474F"
    hanro_border = "#CFD8DC"
    
    if "🌟激アツ" in str(perf_lapeval): 
        hanro_bg = "#FFF3E0"
        hanro_color = "#E65100"
        hanro_border = "#FFE0B2"
    elif "🚨危険" in str(perf_lapeval): 
        hanro_bg = "#FFEBEE"
        hanro_color = "#C62828"
        hanro_border = "#FFCDD2"
        
    if t4 != "-":
        hanro_html = f"""<div style="margin-top: 6px; padding: 8px 12px; background-color: {hanro_bg}; color: {hanro_color}; border: 1px solid {hanro_border}; border-radius: 8px; font-size: 11.5px; {badge_opacity}">
            <div style="font-weight: bold; margin-bottom: 2px;">🏃 坂路: {t4}秒 ({l4}-{l3}-{l2}-{l1})</div>
            <div style="font-size: 11px; font-weight: bold;">評: {perf_lapeval}</div>
        </div>"""
    else:
        hanro_html = f"""<div style="margin-top: 6px; padding: 8px 12px; background-color: #F9F9F9; color: #9E9E9E; border: 1px dashed #E0E0E0; border-radius: 8px; font-size: 11.5px; {badge_opacity}">
            <div style="font-weight: bold; margin-bottom: 2px;">🏃 坂路: データなし</div>
        </div>"""

    # ── ウッド調教表示 ──
    def _fmt_w(v):
        try:
            return f"{float(v):.1f}" if not pd.isna(float(v)) else "-"
        except (TypeError, ValueError):
            return "-"
    w5f_txt = _fmt_w(row_data.get('W5F', float('nan')))
    wl1_txt = _fmt_w(row_data.get('Wラスト1F', float('nan')))
    w_eval    = str(row_data.get('W評価', '-'))
    combo_val = str(row_data.get('調教コンボ', ''))
    if w5f_txt != "-":
        if '🌟W-ラスト11秒台' in w_eval:
            w_bg, w_col, w_bdr = "#FFF3E0", "#E65100", "#FFE0B2"
        elif '✅W-ラスト12.0' in w_eval:
            w_bg, w_col, w_bdr = "#E8F5E9", "#2E7D32", "#A5D6A7"
        else:
            w_bg, w_col, w_bdr = "#ECEFF1", "#37474F", "#CFD8DC"
        wood_html = f"""<div style="margin-top:4px; padding:8px 12px; background-color:{w_bg}; color:{w_col}; border:1px solid {w_bdr}; border-radius:8px; font-size:11.5px; {badge_opacity}">
            <div style="font-weight:bold; margin-bottom:2px;">🌲 ウッド: 5F={w5f_txt}秒 / ラスト={wl1_txt}秒</div>
            <div style="font-size:11px; font-weight:bold;">評: {w_eval}</div>
        </div>"""
    else:
        wood_html = ""
    if combo_val and combo_val not in ('', '坂路激アツ(ウッドなし)', '-'):
        if '🔥' in combo_val:
            cb_bg, cb_col, cb_bdr = "#BF360C", "#FFFFFF", "#E64A19"
        elif '⚡' in combo_val:
            cb_bg, cb_col, cb_bdr = "#E65100", "#FFFFFF", "#FF6D00"
        else:
            cb_bg, cb_col, cb_bdr = "#1565C0", "#FFFFFF", "#0D47A1"
        combo_html = f"""<div style="margin-top:4px; padding:6px 12px; background-color:{cb_bg}; color:{cb_col}; border:1px solid {cb_bdr}; border-radius:8px; font-size:12px; font-weight:bold; text-align:center; {badge_opacity}">{combo_val}</div>"""
    else:
        combo_html = ""

    
    badges_html_list = []
    checks = [
        ("騎手", row_data.get('騎手判定', 'ー')), 
        ("調教", row_data.get('調教師判定', 'ー')), 
        ("馬主", row_data.get('馬主判定', 'ー') if '馬主判定' in row_data.index else 'ー'), 
        ("サイン", row_data.get('配置サイン', 'ー'))
    ]
    for l_t, dec in checks:
        b_h = make_badge_html(l_t, dec)
        if b_h: badges_html_list.append(b_h)
    
    dec_badges_section = f"""<div style="display: flex; flex-direction: column; gap: 2px; margin-top: 4px; {badge_opacity}">{"".join(badges_html_list)}</div>""" if badges_html_list else ""
    
    if horse_key not in st.session_state['partner_cache']:
        st.session_state['partner_cache'][horse_key] = find_all_pair_partners_detailed(row_data, curr_df)
        
    detailed_partners = st.session_state['partner_cache'][horse_key]
    future_partner_html = ""
    if detailed_partners:
        p_list = [f"""<div style="margin-top: 4px; font-size: 11px; line-height: 1.4; background-color: #FFFFFF; padding: 4px 8px; border-radius: 4px; border-left: 3px solid #0066CC; box-shadow: 0 1px 2px rgba(0,0,0,0.05); color: #333333;">{p}</div>""" for p in detailed_partners]
        future_partner_html = f"""
        <div style="margin-top: 8px; padding: 8px; background-color: #EBF3FC; border: 1px solid #B3D4FF; border-radius: 8px; {badge_opacity}">
            <div style="font-weight: bold; font-size: 12px; color: #0052CC; display: flex; align-items: center; gap: 4px; margin-bottom: 4px;">
                <span style="font-size: 14px;">🔮</span><span>同期ペア情報:</span>
            </div>
            <div style="display: flex; flex-direction: column; gap: 2px;">{"".join(p_list)}</div>
        </div>"""
    
    r_num_badge = f"<span style='background-color:#000; color:#FFF; font-size:12px; padding:2px 6px; border-radius:4px; margin-right:4px;'>{r_num}R</span>"
    
    card_html = textwrap.dedent(f"""
    <div class="{card_class}" style="border-left: {border_left_style} !important; padding: 12px; box-shadow: 1px 1px 4px rgba(0,0,0,0.05); font-family: sans-serif; {text_color_style} display: flex; flex-direction: column; justify-content: space-between; overflow: hidden;">
        <div>
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div style="display: flex; align-items: center; gap: 4px;">
                    {r_num_badge}
                    <span style="background-color: {waku_bg}; color: {waku_fg}; border: 2px solid {waku_border}; font-size: 14px; font-weight: bold; padding: 2px 6px; border-radius: 50%; box-shadow: 1px 1px 3px rgba(0,0,0,0.2); display: inline-block; min-width: 26px; text-align: center;">{num_val}</span>
                    <span style="font-size: 17px; font-weight: bold; margin-left:2px;">{name_val}</span>
                </div>
                <div style="text-align: right; {text_color_style}">
                    <span style="font-size: 13px; font-weight: bold;">{odds_txt} <span style="font-size:11px; font-weight:normal; color:#666;">({pop_txt})</span></span>
                    <div style="{points_style} font-size: 14px; font-weight: bold; margin-top: 2px;">{raw_points}点{stars_txt}</div>
                </div>
            </div>
            {haichi_elements_html}
            {perf_section_html}
            {hanro_html}
            {wood_html}
            {combo_html}
            {dec_badges_section}
        </div>
        {future_partner_html}
    </div>
    """).strip()
    
    with st.container(border=False):
        try: st.html(card_html)
        except AttributeError: st.markdown(card_html, unsafe_allow_html=True)
        
        b_cols1 = st.columns(4)
        with b_cols1[0]:
            if st.button("◎本命", key=f"btn_honmei_{horse_key}", use_container_width=True): 
                st.session_state['user_markers'][horse_key] = "◎"
                st.rerun()
        with b_cols1[1]:
            if st.button("○対抗", key=f"btn_taikou_{horse_key}", use_container_width=True): 
                st.session_state['user_markers'][horse_key] = "○"
                st.rerun()
        with b_cols1[2]:
            if st.button("▲単穴", key=f"btn_tanana_{horse_key}", use_container_width=True): 
                st.session_state['user_markers'][horse_key] = "▲"
                st.rerun()
        with b_cols1[3]:
            if st.button("△連下", key=f"btn_renka_{horse_key}", use_container_width=True): 
                st.session_state['user_markers'][horse_key] = "△"
                st.rerun()
        
        b_cols2 = st.columns(3)
        with b_cols2[0]:
            if st.button("☆穴馬", key=f"btn_hoshi_{horse_key}", use_container_width=True): 
                st.session_state['user_markers'][horse_key] = "☆"
                st.rerun()
        with b_cols2[1]:
            if st.button("✖消す", key=f"btn_keshi_{horse_key}", use_container_width=True): 
                st.session_state['user_markers'][horse_key] = "✖"
                st.rerun()
        with b_cols2[2]:
            if st.button("⚪戻す", key=f"btn_clear_{horse_key}", use_container_width=True): 
                st.session_state['user_markers'][horse_key] = "未設定"
                st.rerun()

# 🌟 JSを利用した「絶対潰れない＆横スクロール」のレンダリング関数 🌟
def render_horse_cards_carousel(h_list, selected_venue, curr_df, cards_per_row=3, block_key=None):
    """3枚グリッド＋ページネーション（コールバック方式・rerun不使用）"""
    if not h_list:
        return

    if isinstance(h_list, pd.DataFrame):
        h_list = [row for _, row in h_list.iterrows()]

    total = len(h_list)
    if total == 0:
        return

    # block_key が未指定の場合は馬名ハッシュで固定
    if block_key is None:
        try:
            names = tuple(str(r.get('馬名', i)) if hasattr(r, 'get') else str(i) for i, r in enumerate(h_list[:5]))
            block_key = "cp_" + str(abs(hash(names)))[:10]
        except Exception:
            block_key = "cp_default"

    page_key = f"carousel_page_{block_key}"
    if page_key not in st.session_state:
        st.session_state[page_key] = 0

    # 1ページ = 3列 × 6行 = 最大18頭（1レース分）
    page_size   = cards_per_row * 6
    total_pages = max(1, -(-total // page_size))

    # コールバック関数（rerun不要・ボタン押下時に即座にページ番号を更新）
    def go_prev():
        st.session_state[page_key] = max(0, st.session_state[page_key] - 1)

    def go_next():
        st.session_state[page_key] = min(total_pages - 1, st.session_state[page_key] + 1)

    current_page = min(st.session_state[page_key], total_pages - 1)
    start_idx    = current_page * page_size
    end_idx      = min(start_idx + page_size, total)
    page_items   = h_list[start_idx:end_idx]

    # ページナビ（複数ページある場合のみ）
    if total_pages > 1:
        nav_cols = st.columns([1, 4, 1])
        with nav_cols[0]:
            st.button("◀ 前",
                      key=f"prev_{block_key}",
                      disabled=(current_page == 0),
                      on_click=go_prev,
                      use_container_width=True)
        with nav_cols[1]:
            st.markdown(
                f"<div style='text-align:center;color:#666;font-size:13px;padding-top:6px;'>"
                f"{start_idx+1}〜{end_idx} / {total}頭　"
                f"({current_page+1}/{total_pages}ページ)</div>",
                unsafe_allow_html=True
            )
        with nav_cols[2]:
            st.button("次 ▶",
                      key=f"next_{block_key}",
                      disabled=(current_page >= total_pages - 1),
                      on_click=go_next,
                      use_container_width=True)

    # cards_per_row 枚ずつ行に並べる
    for row_start in range(0, len(page_items), cards_per_row):
        row_items = page_items[row_start:row_start + cards_per_row]
        cols = st.columns(len(row_items))
        for col, row_data in zip(cols, row_items):
            with col:
                render_single_horse_card(row_data, selected_venue, curr_df)
        st.markdown("<div style='margin-bottom:12px;'></div>", unsafe_allow_html=True)


# -------------------------------------------------------------------------
# メイン処理ブロック
# -------------------------------------------------------------------------
st.sidebar.markdown("### 🧬 黄金比能力データベース")
history_df = get_master_history_data()

if history_df is not None and not history_df.empty:
    st.sidebar.success(f"自動ロード完了\n({len(history_df)}件のレコード)")
    min_d = history_df['date'].min()
    max_d = history_df['date'].max()
    st.sidebar.caption(f"DB収録期間: {min_d.strftime('%Y-%m-%d')} 〜 {max_d.strftime('%Y-%m-%d')}")
else:
    st.sidebar.warning("過去実績データベースが見つかりません。")
    uploaded_history = st.sidebar.file_uploader("過去履歴DB-CSVを手動ロード", type=["csv"], key="history_manual")
    if uploaded_history is not None:
        history_df = get_manual_history_data(uploaded_history.getvalue())
        if history_df is not None: 
            st.sidebar.success(f"手動ロード完了: {len(history_df)}件")

st.sidebar.markdown("---")
global_target_date = st.sidebar.date_input("判定基準日（能力算出用）", date.today())
global_target_datetime = pd.to_datetime(global_target_date)

col1, col2, col3 = st.columns(3)
with col1: 
    st.subheader("1. 前日の結果CSV (複数可)")
with col2: 
    st.subheader("2. 当日の出馬表CSV (複数可)")
with col3: 
    st.subheader("3. 坂路調教ラップCSV (任意)")

# ============================================================
# GitHub自動取得 UI
# ============================================================
st.sidebar.markdown("### 🌐 データ取得方法")
data_source = st.sidebar.radio(
    "データソースを選択",
    ["📡 GitHub自動取得（推奨）", "📁 手動アップロード"],
    key="data_source_mode"
)

github_curr_bytes  = None
github_prev_bytes  = None
github_hanro_bytes = None
github_wood_bytes  = None
_curr_name  = "today.csv"
_prev_name  = "prev.csv"
_hanro_name = "train.csv"
_wood_name  = "wood.csv"

if data_source == "📡 GitHub自動取得（推奨）":
    col_g1, col_g2 = st.sidebar.columns(2)
    with col_g1:
        if st.button("🔄 最新データを取得", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
    with col_g2:
        st.caption("1時間キャッシュ")
    (github_curr_bytes, github_prev_bytes, github_hanro_bytes, github_wood_bytes,
     _curr_name, _prev_name, _hanro_name, _wood_name) = load_github_data()
    if github_curr_bytes:
        st.sidebar.success(f"✅ 出馬表：{_curr_name}")
    else:
        st.sidebar.warning("⚠️ 出馬表：today/ にCSVが見つかりません")
    if github_prev_bytes:
        st.sidebar.success(f"✅ 前日結果：{_prev_name}")
    else:
        st.sidebar.info("ℹ️ 前日結果：prev/ なし（任意）")
    if github_hanro_bytes:
        st.sidebar.success(f"✅ 坂路：{_hanro_name}")
    else:
        st.sidebar.info("ℹ️ 坂路：train/ なし（任意）")
    if github_wood_bytes:
        st.sidebar.success(f"✅ ウッド：{_wood_name}")
    else:
        st.sidebar.info("ℹ️ ウッド：wood/ なし（任意）")

st.sidebar.markdown("---")

prev_files     = col1.file_uploader("前日の結果CSVを選択", type=["csv"], key="prev", accept_multiple_files=True)
curr_files     = col2.file_uploader("当日の出馬表CSVを選択", type=["csv"], key="curr", accept_multiple_files=True)
uploaded_hanro = col3.file_uploader("坂路調教ラップCSVを選択", type=["csv"], key="hanro_upload", help="馬名, 年月日, Time1, Lap4... の列を含むこと")

st.sidebar.subheader("4. ウッド調教CSV (任意)")
uploaded_wood = st.sidebar.file_uploader(
    "ウッド調教CSVを選択 (ututo形式)",
    type=["csv"], key="wood_upload",
    help="場所,コース,馬名,5F,4F,Lap1... の列を含むututo出力形式"
)

# GitHub取得データを手動アップロードと同じ形式に変換
if data_source == "📡 GitHub自動取得（推奨）":
    if github_curr_bytes:
        _mock_curr = io.BytesIO(github_curr_bytes)
        _mock_curr.name = _curr_name
        curr_files = [_mock_curr]
    if github_prev_bytes:
        _mock_prev = io.BytesIO(github_prev_bytes)
        _mock_prev.name = _prev_name
        prev_files = [_mock_prev]
    if github_hanro_bytes:
        _mock_hanro = io.BytesIO(github_hanro_bytes)
        _mock_hanro.name = _hanro_name
        uploaded_hanro = _mock_hanro
    if github_wood_bytes:
        _mock_wood = io.BytesIO(github_wood_bytes)
        _mock_wood.name = _wood_name
        uploaded_wood = _mock_wood

curr_state_key  = ",".join([f.name for f in curr_files]) if curr_files else ""
prev_state_key  = ",".join([f.name for f in prev_files]) if prev_files else ""
hanro_state_key = uploaded_hanro.name if uploaded_hanro else ""
wood_state_key  = uploaded_wood.name  if uploaded_wood  else ""
current_combo_key = f"{curr_state_key}_{prev_state_key}_{hanro_state_key}_{wood_state_key}_{global_target_date}" 
if curr_files and st.session_state.get('last_processed_key') != current_combo_key:
    with st.spinner("🏇 データを解析・計算しています...（最初のみ数秒かかります）"):
        st.session_state['partner_cache'] = {}
        
        dfs = []
        for f in curr_files:
            f.seek(0)
            df_sub = None
            for enc in ['utf-8', 'shift_jis', 'cp932']:
                try: 
                    df_sub = pd.read_csv(f, encoding=enc)
                    break
                except Exception: 
                    f.seek(0)
                    continue
            if df_sub is not None: 
                dfs.append(df_sub)
            
        df = pd.concat(dfs, ignore_index=True)
        ODDS_COL_ALIASES = ['単勝オッズ', '単勝', 'オッズ', '単オッズ', '単勝(元値)',
                              'Win Odds', 'win_odds', 'odds', 'Odds', '単勝オッズ(確定)',
                              '予想オッズ', '想定オッズ', '暫定オッズ']
        POP_COL_ALIASES  = ['単勝人気', '人気', '確定人気', '人気順', '人気(確定)',
                             '予想人気', '想定人気', 'popularity']
        rename_cols = {}
        for col in df.columns:
            col_norm = col.strip()
            if col_norm in ODDS_COL_ALIASES and col_norm != 'オッズ':
                rename_cols[col] = 'オッズ'
            elif col_norm in POP_COL_ALIASES and col_norm != '人気':
                rename_cols[col] = '人気'
            elif col_norm == '馬主':
                rename_cols[col] = '馬主(最新/仮想)'
                
        if rename_cols: 
            df = df.rename(columns=rename_cols)
            
        if 'オッズ' not in df.columns:
            df['オッズ'] = np.nan
            _cands = [c for c in df.columns if 'オッズ' in c or '単勝' in c or 'odds' in c.lower()]
            if _cands:
                st.sidebar.warning(f"オッズ列未検出。CSV内の候補: {_cands} \n列名を『オッズ』に変更してください。")
            else:
                st.sidebar.info("CSVにオッズ列がありません（未取得と表示されます）。")
        if '人気' not in df.columns:
            df['人気'] = np.nan
        
        df['馬名'] = df['馬名'].apply(clean_horse_name)
        df = preprocess_and_calculate_haichi(df)
        
        hanro_clean_df = None
        if uploaded_hanro is not None:
            try:
                try: 
                    df_hanro = pd.read_csv(uploaded_hanro, encoding='utf-8')
                except UnicodeDecodeError: 
                    uploaded_hanro.seek(0)
                    df_hanro = pd.read_csv(uploaded_hanro, encoding='cp932')
                    
                df_hanro.columns = df_hanro.columns.str.strip()
                df_hanro['馬名'] = df_hanro['馬名'].apply(clean_horse_name)
                
                if all(col in df_hanro.columns for col in ['Time1', 'Lap4', 'Lap3', 'Lap2', 'Lap1']):
                    def classify_lap(row):
                        l2 = row['Lap2']
                        l1 = row['Lap1']
                        if pd.isna(l2) or pd.isna(l1): 
                            return "-"
                        if l2 > l1:
                            accel_diff = round(l2 - l1, 1)
                            if 12.0 <= l1 <= 12.9:
                                if l2 >= 13.0: return "1 終いのみ12秒台の加速"
                                elif 12.0 <= l2 <= 12.9: return "3 終い2F12秒台まとめの加速"
                            elif l1 <= 11.9:
                                if accel_diff >= 0.5: return "🌟激アツ: 5 終い11秒台の急加速(0.5秒以上)"
                                else: return "5 終い11秒台の加速"
                            return "-"
                        else:
                            decel_diff = round(l1 - l2, 1)
                            if 12.0 <= l2 <= 12.9:
                                if l1 >= 13.0: return "🚨危険(地雷): 2 2Fのみ12秒台の減速"
                                elif 12.0 <= l1 <= 12.9: return "4 終い2F12秒台まとめの減速"
                            elif l2 <= 11.9:
                                if l1 >= 12.0:
                                    if decel_diff <= 0.4: return "🌟激アツ: 6 2Fのみ11秒台の微減速(0.4秒以内)"
                                    else: return "6 2Fのみ11秒台の減速"
                                else: return "終い2F11秒台まとめの減速"
                            return "-"
                            
                    df_hanro['ラップ評価'] = df_hanro.apply(classify_lap, axis=1)
                    df_hanro['Time1'] = pd.to_numeric(df_hanro['Time1'], errors='coerce')
                    df_hanro = df_hanro.sort_values(by=['馬名', 'Time1'], ascending=True)
                    hanro_clean_df = df_hanro.drop_duplicates(subset=['馬名'], keep='first').copy()
                    hanro_clean_df = hanro_clean_df.rename(columns={'Time1': '4Fタイム', 'Lap1': 'ラスト1F'})
            except Exception: 
                pass
            
        if hanro_clean_df is not None:
            df = pd.merge(df, hanro_clean_df[['馬名', '4Fタイム', 'Lap4', 'Lap3', 'Lap2', 'ラスト1F', 'ラップ評価']], on='馬名', how='left')
            df['ラップ評価'] = df['ラップ評価'].fillna("-")
        else:
            for col in ['4Fタイム', 'Lap4', 'Lap3', 'Lap2', 'ラスト1F']: 
                df[col] = np.nan
            df['ラップ評価'] = "-"

        # ── ウッド調教データ処理 ──
        wood_df_parsed = None
        _wood_src = uploaded_wood if uploaded_wood is not None else None
        if _wood_src is not None:
            try:
                _wood_src.seek(0)
            except Exception:
                pass
            wood_df_parsed = parse_wood_csv(_wood_src)
        if wood_df_parsed is not None:
            df = pd.merge(df, wood_df_parsed, on='馬名', how='left')
            df['W評価'] = df['W評価'].fillna("-")
        else:
            for col in ['W5F', 'W4F', 'Wラスト1F']:
                df[col] = np.nan
            df['W評価'] = "-"
        df['調教コンボ'] = df.apply(
            lambda r: classify_wood_combo(r.get('ラップ評価', '-'), r.get('W評価', '-')), axis=1
        )

        if history_df is not None and not history_df.empty:
            df = apply_performance_levels(df, history_df, global_target_datetime)
        else:
            for col in ['総合指数', 'レベル点', '自力点', 'ボーナス減点', '前走着順', 'レース間隔', '好走/次走あり', '前走着差', '前走脚質', '長期休養フラグ', '前走日付']:
                if col in ['レース間隔', '好走/次走あり', '前走着差', '前走脚質', '長期休養フラグ', '前走日付']:
                    df[col] = '-'
                else:
                    df[col] = np.nan

        owabi_riders = set()
        if prev_files:
            prev_dfs = []
            for f in prev_files:
                f.seek(0)
                for enc in ['utf-8', 'shift_jis', 'cp932']:
                    try: 
                        df_sub = pd.read_csv(f, encoding=enc)
                        break
                    except Exception: 
                        f.seek(0)
                        continue
                if df_sub is not None: 
                    prev_dfs.append(df_sub)
                    
            if prev_dfs:
                prev_df = pd.concat(prev_dfs, ignore_index=True)
                rename_prev = {}
                for col in prev_df.columns:
                    if col in ['単勝オッズ', '単勝', 'オッズ', '単オッズ'] and col != 'オッズ': 
                        rename_prev[col] = 'オッズ'
                    elif col in ['単勝人気', '人気', '確定人気'] and col != '人気': 
                        rename_prev[col] = '人気'
                    elif col == '馬主':
                        rename_prev[col] = '馬主(最新/仮想)'
                        
                if rename_prev: 
                    prev_df = prev_df.rename(columns=rename_prev)
                    
                prev_df = preprocess_and_calculate_haichi(prev_df)
                owabi_riders = extract_owabi_riders(prev_df)
                
        st.session_state['cached_owabi_riders'] = owabi_riders

        df = judge_yellow_and_pairs(df, target_col='騎手')
        df = judge_yellow_and_pairs(df, target_col='調教師')
        if '馬主(最新/仮想)' in df.columns: 
            df = judge_yellow_and_pairs(df, target_col='馬主(最新/仮想)')
            
        df, _ = judge_blue_coating(df, target_col='騎手')
        df, _ = judge_blue_coating(df, target_col='調教師')
        if '馬主(最新/仮想)' in df.columns: 
            df, _ = judge_blue_coating(df, target_col='馬主(最新/仮想)')
            
        for col_p in ['騎手_ペア', '調教師_ペア']:
            if col_p in df.columns: 
                df[col_p] = df[col_p].apply(format_condensed_pairs)
                
        if '馬主(最新/仮想)_ペア' in df.columns: 
            df['馬主(最新/仮想)_ペア'] = df['馬主(最新/仮想)_ペア'].apply(format_condensed_pairs)
            
        df['お詫び好走候補'] = df['騎手'].apply(lambda x: x in owabi_riders)
        df = calculate_haichi_features(df)
        
        def get_haichi_sign_label(row):
            signs = []
            is_blue = row.get('騎手_青塗', False) or row.get('調教師_青塗', False) or row.get('馬主(最新/仮想)_青塗', False)
            if is_blue: 
                signs.append("✨🟦 青塗本体")
            if row.get('is_symmetry', 0.0) == 1.0: 
                signs.append("🔄 対称")
            if row.get('next_to_symmetry', 0.0) == 1.0: 
                signs.append("↔️ 隣馬(対称)")
            if row.get('next_to_blue', 0.0) == 1.0: 
                signs.append("🟦 青塗隣馬")
                
            if signs:
                return " / ".join(signs)
            else:
                return "ー"
            
        df['配置サイン'] = df.apply(get_haichi_sign_label, axis=1)

        df['騎手判定'] = "ー"
        df['調教師判定'] = "ー"
        df['馬主判定'] = "ー"
        
        def safe_float(x):
            try:
                import unicodedata as _ud
                s = _ud.normalize('NFKC', str(x))
                s = s.replace(',', '').replace('倍', '').replace(' ', '').strip()
                return float(s) if s not in ('', 'nan', 'None', '---', 'ー', '-') else 0.0
            except Exception:
                return 0.0

        df['オッズ'] = df['オッズ'].apply(lambda x: safe_float(x) if safe_float(x) > 0 else float('nan'))
        df['temp_odds'] = df['オッズ'].fillna(0.0)

        for idx, row in df.iterrows():
            r_num = row['Ｒ']
            odds_val = row['temp_odds']
            my_venue = row['場所']
            
            # 騎手判定
            if row['騎手_青塗'] or row['騎手_黄塗'] or row['お詫び好走候補']:
                rider_name = row['騎手']
                rider_races = df[df['騎手'] == rider_name].sort_values(by='Ｒ')
                prev_rider_races = rider_races[(rider_races['場所'] == my_venue) & (rider_races['Ｒ'] < r_num)]
                
                if row['騎手_青塗']:
                    first_blue_race = rider_races.iloc[0]['Ｒ']
                    curr_num = int(row['馬番'])
                    neighbors = df[(df['場所'] == my_venue) & (df['Ｒ'] == r_num) & (df['馬番'].isin([curr_num - 1, curr_num + 1]))]
                    my_pop = int(row['人気']) if pd.notnull(row['人気']) else 99
                    
                    neighbor_has_pair = False
                    for _, n_row in neighbors.iterrows():
                        if n_row['騎手_黄塗'] or n_row['調教師_黄塗']:
                            neighbor_has_pair = True
                            
                    is_my_pop_top = False
                    for _, n_row in neighbors.iterrows():
                        n_pop = int(n_row['人気']) if pd.notnull(n_row['人気']) else 99
                        if my_pop < n_pop:
                            is_my_pop_top = True
                    
                    if r_num == first_blue_race:
                        if is_my_pop_top: 
                            df.at[idx, '騎手判定'] = "▲ 青塗1鞍目先買いリスク"
                        elif neighbor_has_pair: 
                            df.at[idx, '騎手判定'] = "△ 青塗隣馬ペアあり(オッズ49.9倍以下)"
                        else: 
                            df.at[idx, '騎手判定'] = "✖ 青塗先買いリスク(見送り)"
                    else:
                        if prev_rider_races.empty: 
                            df.at[idx, '騎手判定'] = "▲ 青塗先買い(前走未確定)"
                        else:
                            last_prev = prev_rider_races.iloc[-1]
                            lp_v = last_prev['場所']
                            lp_r = last_prev['Ｒ']
                            lp_c1 = st.session_state['saved_chaku'].get(f"c1_{lp_v}_{lp_r}")
                            lp_c2 = st.session_state['saved_chaku'].get(f"c2_{lp_v}_{lp_r}")
                            lp_c3 = st.session_state['saved_chaku'].get(f"c3_{lp_v}_{lp_r}")
                            
                            if lp_c1 is None and lp_c2 is None and lp_c3 is None: 
                                df.at[idx, '騎手判定'] = "▲ 青塗先買い(前走未確定)"
                            else:
                                if int(last_prev['馬番']) in [lp_c1, lp_c2, lp_c3]: 
                                    df.at[idx, '騎手判定'] = "✖ 青塗見送り(好走済)"
                                else: 
                                    df.at[idx, '騎手判定'] = "〇 青塗凡走後狙い(絶好の狙い目)"
                                    
                elif row['騎手_黄塗']:
                    yellow_races = rider_races[(rider_races['騎手_黄塗']) & (rider_races['場所'] == my_venue)].sort_values(by='Ｒ')
                    if not has_previous_pair_race(row.get('騎手_ペア', ''), r_num, my_venue): 
                        df.at[idx, '騎手判定'] = "▲ 先買いリスクあり"
                    else:
                        if prev_rider_races.empty: 
                            df.at[idx, '騎手判定'] = "▲ 黄色前走未確定"
                        else:
                            last_prev = prev_rider_races.iloc[-1]
                            lp_v = last_prev['場所']
                            lp_r = last_prev['Ｒ']
                            lp_c1 = st.session_state['saved_chaku'].get(f"c1_{lp_v}_{lp_r}")
                            lp_c2 = st.session_state['saved_chaku'].get(f"c2_{lp_v}_{lp_r}")
                            lp_c3 = st.session_state['saved_chaku'].get(f"c3_{lp_v}_{lp_r}")
                            
                            if lp_c1 is None and lp_c2 is None and lp_c3 is None: 
                                df.at[idx, '騎手判定'] = "▲ 黄色前走未確定"
                            else:
                                is_lp_hit = False
                                if int(last_prev['馬番']) in [lp_c1, lp_c2, lp_c3]:
                                    is_lp_hit = True
                                    
                                is_double_fail = False
                                
                                if len(prev_rider_races) >= 2:
                                    lp2 = prev_rider_races.iloc[-2]
                                    lp2_v = lp2['場所']
                                    lp2_r = lp2['Ｒ']
                                    lp2_c1 = st.session_state['saved_chaku'].get(f"c1_{lp2_v}_{lp2_r}")
                                    lp2_c2 = st.session_state['saved_chaku'].get(f"c2_{lp2_v}_{lp2_r}")
                                    lp2_c3 = st.session_state['saved_chaku'].get(f"c3_{lp2_v}_{lp2_r}")
                                    
                                    if lp2_c1 is not None and lp_c1 is not None:
                                        is_lp2_hit = False
                                        if int(lp2['馬番']) in [lp2_c1, lp2_c2, lp2_c3]:
                                            is_lp2_hit = True
                                            
                                        if not is_lp_hit and not is_lp2_hit: 
                                            is_double_fail = True
                                            
                                if is_double_fail: 
                                    df.at[idx, '騎手判定'] = "◆ 共に凡走(紐警戒)"
                                elif is_lp_hit: 
                                    df.at[idx, '騎手判定'] = "✖ 好走済みで後のレースは狙えない"
                                else:
                                    if len(yellow_races) >= 3 and row['騎手_厩舎同ペア']: 
                                        df.at[idx, '騎手判定'] = "〇 狙いたいケース(前走凡走・騎手厩舎同一ペア)"
                                    else: 
                                        df.at[idx, '騎手判定'] = "〇 狙いたいケース(前走凡走)"
            
            # 調教師判定
            if row['調教師_青塗'] or row['調教師_黄塗']:
                stable_name = row['調教師']
                stable_races = df[df['調教師'] == stable_name].sort_values(by='Ｒ')
                prev_stable_races = stable_races[(stable_races['場所'] == my_venue) & (stable_races['Ｒ'] < r_num)]
                
                if row['調教師_青塗']:
                    first_blue_race = stable_races.iloc[0]['Ｒ']
                    curr_num = int(row['馬番'])
                    neighbors = df[(df['場所'] == my_venue) & (df['Ｒ'] == r_num) & (df['馬番'].isin([curr_num - 1, curr_num + 1]))]
                    my_pop = int(row['人気']) if pd.notnull(row['人気']) else 99
                    
                    neighbor_has_pair = False
                    for _, n_row in neighbors.iterrows():
                        if n_row['騎手_黄塗'] or n_row['調教師_黄塗']:
                            neighbor_has_pair = True
                            
                    is_my_pop_top = False
                    for _, n_row in neighbors.iterrows():
                        n_pop = int(n_row['人気']) if pd.notnull(n_row['人気']) else 99
                        if my_pop < n_pop:
                            is_my_pop_top = True
                            
                    if r_num == first_blue_race:
                        if is_my_pop_top: 
                            df.at[idx, '調教師判定'] = "▲ 青塗1鞍目先買いリスク"
                        elif neighbor_has_pair: 
                            df.at[idx, '調教師判定'] = "△ 青塗隣馬ペアあり(オッズ49.9倍以下)"
                        else: 
                            df.at[idx, '調教師判定'] = "✖ 青塗先買いリスク(見送り)"
                    else:
                        if prev_stable_races.empty: 
                            df.at[idx, '調教師判定'] = "▲ 青塗先買い(前走未確定)"
                        else:
                            last_prev = prev_stable_races.iloc[-1]
                            lp_v = last_prev['場所']
                            lp_r = last_prev['Ｒ']
                            lp_c1 = st.session_state['saved_chaku'].get(f"c1_{lp_v}_{lp_r}")
                            lp_c2 = st.session_state['saved_chaku'].get(f"c2_{lp_v}_{lp_r}")
                            lp_c3 = st.session_state['saved_chaku'].get(f"c3_{lp_v}_{lp_r}")
                            
                            if lp_c1 is None and lp_c2 is None and lp_c3 is None: 
                                df.at[idx, '調教師判定'] = "▲ 青塗先買い(前走未確定)"
                            else:
                                lp_num = int(last_prev['馬番'])
                                is_lp_sym = False
                                if (last_prev.get('is_symmetry', 0.0) == 1.0) or (last_prev.get('next_to_symmetry', 0.0) == 1.0):
                                    is_lp_sym = True
                                    
                                is_lp_hit = False
                                if is_lp_sym: 
                                    if (lp_num in [lp_c1, lp_c2, lp_c3]) or ((lp_num + 1) in [lp_c1, lp_c2, lp_c3]) or ((lp_num - 1) in [lp_c1, lp_c2, lp_c3]):
                                        is_lp_hit = True
                                else: 
                                    if lp_num in [lp_c1, lp_c2, lp_c3]:
                                        is_lp_hit = True
                                        
                                if is_lp_hit: 
                                    df.at[idx, '調教師判定'] = "✖ 青塗見送り(好走済)"
                                else: 
                                    df.at[idx, '調教師判定'] = "〇 青塗凡走後狙い(絶好の狙い目)"
                                    
                elif row['調教師_黄塗']:
                    yellow_races = stable_races[(stable_races['調教師_黄塗']) & (stable_races['場所'] == my_venue)].sort_values(by='Ｒ')
                    if not has_previous_pair_race(row.get('調教師_ペア', ''), r_num, my_venue): 
                        df.at[idx, '調教師判定'] = "▲ 先買いリスクあり"
                    else:
                        if prev_stable_races.empty: 
                            df.at[idx, '調教師判定'] = "▲ 黄色前走未確定"
                        else:
                            last_prev = prev_stable_races.iloc[-1]
                            lp_v = last_prev['場所']
                            lp_r = last_prev['Ｒ']
                            lp_c1 = st.session_state['saved_chaku'].get(f"c1_{lp_v}_{lp_r}")
                            lp_c2 = st.session_state['saved_chaku'].get(f"c2_{lp_v}_{lp_r}")
                            lp_c3 = st.session_state['saved_chaku'].get(f"c3_{lp_v}_{lp_r}")
                            
                            if lp_c1 is None and lp_c2 is None and lp_c3 is None: 
                                df.at[idx, '調教師判定'] = "▲ 黄色前走未確定"
                            else:
                                lp_num = int(last_prev['馬番'])
                                is_lp_sym = False
                                if (last_prev.get('is_symmetry', 0.0) == 1.0) or (last_prev.get('next_to_symmetry', 0.0) == 1.0):
                                    is_lp_sym = True
                                    
                                is_lp_hit = False
                                if is_lp_sym: 
                                    if (lp_num in [lp_c1, lp_c2, lp_c3]) or ((lp_num + 1) in [lp_c1, lp_c2, lp_c3]) or ((lp_num - 1) in [lp_c1, lp_c2, lp_c3]):
                                        is_lp_hit = True
                                else: 
                                    if lp_num in [lp_c1, lp_c2, lp_c3]:
                                        is_lp_hit = True
                                        
                                is_double_fail = False
                                
                                if len(prev_stable_races) >= 2:
                                    lp2 = prev_stable_races.iloc[-2]
                                    lp2_v = lp2['場所']
                                    lp2_r = lp2['Ｒ']
                                    lp2_c1 = st.session_state['saved_chaku'].get(f"c1_{lp2_v}_{lp2_r}")
                                    lp2_c2 = st.session_state['saved_chaku'].get(f"c2_{lp2_v}_{lp2_r}")
                                    lp2_c3 = st.session_state['saved_chaku'].get(f"c3_{lp2_v}_{lp2_r}")
                                    
                                    if lp2_c1 is not None and lp_c1 is not None:
                                        lp2_num = int(lp2['馬番'])
                                        is_lp2_sym = False
                                        if (lp2.get('is_symmetry', 0.0) == 1.0) or (lp2.get('next_to_symmetry', 0.0) == 1.0):
                                            is_lp2_sym = True
                                            
                                        is_lp2_hit = False
                                        if is_lp2_sym: 
                                            if (lp2_num in [lp2_c1, lp2_c2, lp2_c3]) or ((lp2_num + 1) in [lp2_c1, lp2_c2, lp2_c3]) or ((lp2_num - 1) in [lp2_c1, lp2_c2, lp2_c3]):
                                                is_lp2_hit = True
                                        else: 
                                            if lp2_num in [lp2_c1, lp2_c2, lp2_c3]:
                                                is_lp2_hit = True
                                                
                                        if not is_lp_hit and not is_lp2_hit: 
                                            is_double_fail = True
                                            
                                if is_double_fail: 
                                    df.at[idx, '調教師判定'] = "◆ 共に凡走(紐警戒)"
                                elif is_lp_hit: 
                                    df.at[idx, '調教師判定'] = "✖ 好走済みで後のレースは狙えない"
                                else:
                                    if len(yellow_races) >= 3 and row['調教師_厩舎同ペア']: 
                                        df.at[idx, '調教師判定'] = "〇 狙いたいケース(前走凡走・騎手厩舎同一ペア)"
                                    else: 
                                        df.at[idx, '調教師判定'] = "〇 狙いたいケース(前走凡走)"

            # 馬主判定
            if '馬主(最新/仮想)' in row and (row.get('馬主(最新/仮想)_青塗', False) or row.get('馬主(最新/仮想)_黄塗', False)):
                owner_name = row['馬主(最新/仮想)']
                owner_races = df[df['馬主(最新/仮想)'] == owner_name].sort_values(by='Ｒ')
                prev_owner_races = owner_races[(owner_races['場所'] == my_venue) & (owner_races['Ｒ'] < r_num)]
                
                if row.get('馬主(最新/仮想)_青塗', False):
                    first_blue_race = owner_races.iloc[0]['Ｒ']
                    curr_num = int(row['馬番'])
                    neighbors = df[(df['場所'] == my_venue) & (df['Ｒ'] == r_num) & (df['馬番'].isin([curr_num - 1, curr_num + 1]))]
                    my_pop = int(row['人気']) if pd.notnull(row['人気']) else 99
                    
                    neighbor_has_pair = False
                    for _, n_row in neighbors.iterrows():
                        if n_row.get('馬主(最新/仮想)_黄塗', False):
                            neighbor_has_pair = True
                            
                    is_my_pop_top = False
                    for _, n_row in neighbors.iterrows():
                        n_pop = int(n_row['人気']) if pd.notnull(n_row['人気']) else 99
                        if my_pop < n_pop:
                            is_my_pop_top = True
                            
                    if r_num == first_blue_race:
                        if is_my_pop_top: 
                            df.at[idx, '馬主判定'] = "▲ 青塗1鞍目先買いリスク"
                        elif neighbor_has_pair: 
                            df.at[idx, '馬主判定'] = "△ 青塗隣馬ペアあり(オッズ49.9倍以下)"
                        else: 
                            df.at[idx, '馬主判定'] = "✖ 青塗先買いリスク(見送り)"
                    else:
                        if prev_owner_races.empty: 
                            df.at[idx, '馬主判定'] = "▲ 青塗先買い(前走未確定)"
                        else:
                            last_prev = prev_owner_races.iloc[-1]
                            lp_v = last_prev['場所']
                            lp_r = last_prev['Ｒ']
                            lp_c1 = st.session_state['saved_chaku'].get(f"c1_{lp_v}_{lp_r}")
                            lp_c2 = st.session_state['saved_chaku'].get(f"c2_{lp_v}_{lp_r}")
                            lp_c3 = st.session_state['saved_chaku'].get(f"c3_{lp_v}_{lp_r}")
                            
                            if lp_c1 is None and lp_c2 is None and lp_c3 is None: 
                                df.at[idx, '馬主判定'] = "▲ 青塗先買い(前走未確定)"
                            else:
                                is_lp_hit = False
                                if int(last_prev['馬番']) in [lp_c1, lp_c2, lp_c3]:
                                    is_lp_hit = True
                                    
                                if is_lp_hit: 
                                    df.at[idx, '馬主判定'] = "✖ 青塗見送り(好走済)"
                                else: 
                                    df.at[idx, '馬主判定'] = "〇 青塗凡走後狙い(絶好の狙い目)"
                                    
                elif row.get('馬主(最新/仮想)_黄塗', False):
                    yellow_races = owner_races[(owner_races['馬主(最新/仮想)_黄塗']) & (owner_races['場所'] == my_venue)].sort_values(by='Ｒ')
                    if not has_previous_pair_race(row.get('馬主(最新/仮想)_ペア', ''), r_num, my_venue): 
                        df.at[idx, '馬主判定'] = "▲ 先買いリスクあり"
                    else:
                        if prev_owner_races.empty: 
                            df.at[idx, '馬主判定'] = "▲ 黄色前走未確定"
                        else:
                            last_prev = prev_owner_races.iloc[-1]
                            lp_v = last_prev['場所']
                            lp_r = last_prev['Ｒ']
                            lp_c1 = st.session_state['saved_chaku'].get(f"c1_{lp_v}_{lp_r}")
                            lp_c2 = st.session_state['saved_chaku'].get(f"c2_{lp_v}_{lp_r}")
                            lp_c3 = st.session_state['saved_chaku'].get(f"c3_{lp_v}_{lp_r}")
                            
                            if lp_c1 is None and lp_c2 is None and lp_c3 is None: 
                                df.at[idx, '馬主判定'] = "▲ 黄色前走未確定"
                            else:
                                is_lp_hit = False
                                if int(last_prev['馬番']) in [lp_c1, lp_c2, lp_c3]:
                                    is_lp_hit = True
                                    
                                is_double_fail = False
                                
                                if len(prev_owner_races) >= 2:
                                    lp2 = prev_owner_races.iloc[-2]
                                    lp2_v = lp2['場所']
                                    lp2_r = lp2['Ｒ']
                                    lp2_c1 = st.session_state['saved_chaku'].get(f"c1_{lp2_v}_{lp2_r}")
                                    lp2_c2 = st.session_state['saved_chaku'].get(f"c2_{lp2_v}_{lp2_r}")
                                    lp2_c3 = st.session_state['saved_chaku'].get(f"c3_{lp2_v}_{lp2_r}")
                                    
                                    if lp2_c1 is not None and lp_c1 is not None:
                                        is_lp2_hit = False
                                        if int(lp2['馬番']) in [lp2_c1, lp2_c2, lp2_c3]:
                                            is_lp2_hit = True
                                            
                                        if not is_lp_hit and not is_lp2_hit: 
                                            is_double_fail = True
                                            
                                if is_double_fail: 
                                    df.at[idx, '馬主判定'] = "◆ 共に凡走(紐警戒)"
                                elif is_lp_hit: 
                                    df.at[idx, '馬主判定'] = "✖ 好走済みで後のレースは狙えない"
                                else:
                                    if len(yellow_races) >= 3: 
                                        df.at[idx, '馬主判定'] = "〇 狙いたいケース(前走凡走)"
                                    else: 
                                        df.at[idx, '馬主判定'] = "〇 狙いたいケース(前走凡走)"
                                    
            for col_rec in ['騎手判定', '調教師判定', '馬主判定']:
                if col_rec in df.columns:
                    current_rec = df.at[idx, col_rec]
                    if odds_val >= 50.0 and ("〇" in current_rec or "△" in current_rec or "◆" in current_rec):
                        df.at[idx, col_rec] = "△ 狙い(大穴50倍以上)"

        df['配置ポイント'] = df.apply(calculate_placement_points, axis=1)
        
        st.session_state['fully_processed_df'] = df
        st.session_state['last_processed_key'] = current_combo_key
        st.session_state['partner_cache'] = {}
        st.session_state['user_markers'] = {}
        st.session_state['saved_chaku'] = {}

# =========================================================================
# UIの描画処理（キャッシュデータのみを使用）
# =========================================================================
curr_df = st.session_state.get('fully_processed_df', pd.DataFrame())
owabi_riders = st.session_state.get('cached_owabi_riders', set())

if not curr_df.empty:
    venue_list = sorted(curr_df['場所'].unique())
    
    selected_venue = st.selectbox(
        "場所を選択してください", 
        venue_list, 
        index=venue_list.index(st.session_state.get('selected_venue', venue_list[0])) if st.session_state.get('selected_venue') in venue_list else 0,
        key='venue_selector'
    )
    st.session_state['selected_venue'] = selected_venue

    st.sidebar.markdown("### 🔄 オッズ自動同期")
    has_jra_race = any(v in JRA_VENUES for v in curr_df['場所'].unique())
    if has_jra_race:
        st.sidebar.info(f"現在表示中の【{selected_venue}】の最新オッズを同期します。")
        if st.sidebar.button(f"🏆 {selected_venue}のオッズを更新", use_container_width=True):
            if selected_venue not in JRA_VENUES: 
                st.sidebar.error(f"{selected_venue}はJRAの競馬場ではないため取得できません。")
            else:
                progress_bar = st.sidebar.progress(0)
                status_text = st.sidebar.empty()
                r_list = sorted(curr_df[curr_df['場所'] == selected_venue]['Ｒ'].unique())
                total_steps = len(r_list)
                step = 0
                success_count = 0
                date_val = str(curr_df['日付S'].iloc[0])
                
                for r_num in r_list:
                    step += 1
                    progress_bar.progress(min(step / total_steps, 1.0))
                    status_text.text(f"取得中: {selected_venue} {r_num}R")
                    scraped_odds = scrape_yahoo_odds_jra(date_val, selected_venue, r_num)
                    
                    if scraped_odds:
                        success_count += 1
                        for h_num, odd_val in scraped_odds.items():
                            mask = (st.session_state['fully_processed_df']['場所'] == selected_venue) & (st.session_state['fully_processed_df']['Ｒ'] == r_num) & (st.session_state['fully_processed_df']['馬番'] == h_num)
                            st.session_state['fully_processed_df'].loc[mask, 'オッズ'] = odd_val
                            
                            if odd_val != "---":
                                st.session_state['fully_processed_df'].loc[mask, 'temp_odds'] = float(odd_val)
                            else:
                                st.session_state['fully_processed_df'].loc[mask, 'temp_odds'] = 999.0
                                
                        race_mask = (st.session_state['fully_processed_df']['場所'] == selected_venue) & (st.session_state['fully_processed_df']['Ｒ'] == r_num)
                        race_subset = st.session_state['fully_processed_df'].loc[race_mask].copy().sort_values(by='temp_odds')
                        
                        pop_val = 1
                        for r_sub_idx in race_subset.index:
                            st.session_state['fully_processed_df'].at[r_sub_idx, '人気'] = pop_val
                            pop_val += 1
                            
                    time.sleep(0.3)
                    
                progress_bar.empty()
                status_text.empty()
                
                if success_count > 0: 
                    st.sidebar.success(f"【{selected_venue}】のオッズ同期が完了しました。")
                else: 
                    st.sidebar.error("オッズの取得に失敗しました。")
                    
                st.session_state['force_recalc_odds'] = True
                st.rerun()
    else: 
        st.sidebar.warning("※JRA（中央競馬）のデータが検出されないためオッズ同期は使用できません。")

    if owabi_riders: 
        st.info(f"前日から引き継いだ【お詫び好走候補の騎手】: {', '.join(owabi_riders)}")
        
    st.subheader("🎯 配置・カラー・能力判定結果")

    filtered_df_venue = curr_df[curr_df['場所'] == selected_venue].copy()
    race_list = sorted(filtered_df_venue['Ｒ'].unique())

    tab_list = [f"{r}R" for r in race_list] + ["🎯 予想印まとめ", "📊 黄金比能力比較", "📊 本日の集計"]
    st.markdown("### 🔍 表示メニュー選択")
    
    # 【改修】key='current_tab' で session_state と直結 → 1回の選択で即座に反映
    if 'current_tab' not in st.session_state or st.session_state['current_tab'] not in tab_list:
        st.session_state['current_tab'] = tab_list[0]

    selected_tab = st.radio(
        "メニュー",
        tab_list,
        key='current_tab',
        horizontal=True,
        label_visibility="collapsed"
    )
    st.markdown("---")
    
    def update_chaku(key_name):
        st.session_state['saved_chaku'][key_name] = st.session_state[key_name]
        st.session_state['partner_cache'] = {}

    if selected_tab.endswith("R"):
        r_num = int(selected_tab.replace("R", ""))
        display_df = filtered_df_venue[filtered_df_venue['Ｒ'] == r_num].copy()
        display_df = display_df.sort_values(by='馬番')

        # 印状況の集計と表示
        marks_dict = {"◎": [], "○": [], "▲": [], "△": [], "☆": [], "✖": []}
        for k, v in st.session_state['user_markers'].items():
            if k.startswith(f"{selected_venue}_{r_num}_") and v in marks_dict:
                marks_dict[v].append(int(k.split('_')[-1]))
                
        for m in marks_dict: marks_dict[m].sort()
        mark_colors = {"◎": "#FF5722", "○": "#2196F3", "▲": "#4CAF50", "△": "#FFC107", "☆": "#9C27B0", "✖": "#757575"}
        
        mark_html_parts = []
        for m in ["◎", "○", "▲", "△", "☆", "✖"]:
            nums_str = ",".join(map(str, marks_dict[m])) if marks_dict[m] else "なし"
            mark_html_parts.append(f"<span style='margin-right:15px; font-size:15px;'><strong style='color:{mark_colors[m]}; font-size:18px;'>{m}</strong>: {nums_str}</span>")
            
        st.markdown(f"<div style='background-color:#FFFFFF; padding:10px 15px; border-radius:8px; border:2px solid #E0E0E0; margin-bottom:15px;'>{''.join(mark_html_parts)}</div>", unsafe_allow_html=True)

        st.markdown("##### 🏁 確定着順（1〜3着）の入力")
        col_c1, col_c2, col_c3 = st.columns(3)
        horse_options = [None] + sorted(list(display_df['馬番'].astype(int).unique()))
        
        with col_c1:
            k1 = f"c1_{selected_venue}_{r_num}"
            v1 = st.session_state['saved_chaku'].get(k1)
            chaku_1 = st.selectbox("1着の馬番", horse_options, index=horse_options.index(v1) if v1 in horse_options else 0, key=k1, on_change=update_chaku, args=(k1,), format_func=lambda x: f"{x}番" if x is not None else "未確定")
        with col_c2:
            k2 = f"c2_{selected_venue}_{r_num}"
            v2 = st.session_state['saved_chaku'].get(k2)
            chaku_2 = st.selectbox("2着の馬番", horse_options, index=horse_options.index(v2) if v2 in horse_options else 0, key=k2, on_change=update_chaku, args=(k2,), format_func=lambda x: f"{x}番" if x is not None else "未確定")
        with col_c3:
            k3 = f"c3_{selected_venue}_{r_num}"
            v3 = st.session_state['saved_chaku'].get(k3)
            chaku_3 = st.selectbox("3着の馬番", horse_options, index=horse_options.index(v3) if v3 in horse_options else 0, key=k3, on_change=update_chaku, args=(k3,), format_func=lambda x: f"{x}番" if x is not None else "未確定")
        
        st.markdown("---")
        
        # 🌟 ここから【横スクロール・予想ボード表示】 🌟
        unrated, core, secondary, ignored = [], [], [], []
        
        for idx, row in display_df.iterrows():
            num_val = int(row['馬番'])
            h_key = f"{selected_venue}_{r_num}_{num_val}"
            m = st.session_state['user_markers'].get(h_key, "未設定")
            
            if m == "未設定": unrated.append(row)
            elif m in ["◎", "○", "▲"]: core.append(row)
            elif m in ["△", "☆"]: secondary.append(row)
            elif m == "✖": ignored.append(row)
            
        st.markdown("### 🎯 直感予想ボード")
        st.markdown("◀ カードを横にスワイプ（スクロール）して仕分けを行ってください。")
        
        # 未評価レーン
        st.markdown(f"#### 📝 1. 未評価 `{len(unrated)}頭`")
        if unrated:
            render_horse_cards_carousel(unrated, selected_venue, curr_df, block_key="unrated")
        else:
            st.info("すべての馬の仕分けが完了しました！")
            
        st.markdown("<hr style='margin: 1em 0; border: none; border-bottom: 1px solid #ccc;'/>", unsafe_allow_html=True)
        
        # 軸・本命レーン
        st.markdown(f"#### 🎯 2. 軸・本命候補 (◎ ○ ▲) `{len(core)}頭`")
        if core:
            render_horse_cards_carousel(core, selected_venue, curr_df, block_key="core")
        else:
            st.info("軸・本命候補はいません。")
            
        st.markdown("<hr style='margin: 1em 0; border: none; border-bottom: 1px solid #ccc;'/>", unsafe_allow_html=True)
        
        # ヒモ・穴レーン
        st.markdown(f"#### ⚡ 3. ヒモ・穴候補 (△ ☆) `{len(secondary)}頭`")
        if secondary:
            render_horse_cards_carousel(secondary, selected_venue, curr_df, block_key="secondary")
        else:
            st.info("ヒモ・穴候補はいません。")

        # 消しレーン（折りたたみ）
        st.markdown("---")
        with st.expander(f"🔽 4. 消した馬を表示する (✖) `{len(ignored)}頭`"):
            if ignored:
                render_horse_cards_carousel(ignored, selected_venue, curr_df, block_key="ignored")
            else:
                st.info("消した馬はいません。")
                        
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔄 このレースの手動攻略マークをすべてクリアする", use_container_width=True, key=f"clear_markers_{selected_venue}_{r_num}"):
            keys_to_clear = [k for k in st.session_state['user_markers'].keys() if k.startswith(f"{selected_venue}_{r_num}_")]
            for k in keys_to_clear: del st.session_state['user_markers'][k]
            st.rerun()

    # 🌟 ここから【予想印まとめタブ】 🌟
    elif selected_tab == "🎯 予想印まとめ":
        st.markdown(f"### 🎯 予想印まとめ ({selected_venue})")
        st.markdown("本日印をつけた馬の最終確認リストです。")
        
        has_any = False
        for r_num in sorted(curr_df[curr_df['場所'] == selected_venue]['Ｒ'].unique()):
            race_df = curr_df[(curr_df['場所'] == selected_venue) & (curr_df['Ｒ'] == r_num)].sort_values('馬番')
            
            marked_rows = []
            for idx, row in race_df.iterrows():
                num_val = int(row['馬番'])
                h_key = f"{selected_venue}_{r_num}_{num_val}"
                m = st.session_state['user_markers'].get(h_key, "未設定")
                if m not in ["未設定", "✖"]:
                    marked_rows.append((row, m))
                    
            if marked_rows:
                has_any = True
                sort_order = {"◎":0, "○":1, "▲":2, "△":3, "☆":4}
                marked_rows.sort(key=lambda x: sort_order.get(x[1], 99))
                
                st.markdown(f"#### 🏁 {r_num}R の買い目候補")
                
                # ここも横スクロールで統一
                rows_to_render = [item[0] for item in marked_rows]
                render_horse_cards_carousel(rows_to_render, selected_venue, curr_df, block_key="marked")
                st.markdown("<hr style='margin: 1em 0; border: none; border-bottom: 1px dashed #ccc;'/>", unsafe_allow_html=True)
                
        if not has_any:
            st.info("💡 まだ印（◎○▲△☆）をつけた馬がいません。各レースから馬を選択してください。")

    elif selected_tab == "📊 黄金比能力比較":
        st.markdown(f"### 📊 黄金比バランス指数・調教比較 ({selected_venue})")
        st.markdown("前走の相手関係、タイム差（着差）、脚質データ、ローテーション、長期休養フラグに加え、坂路調教の4Fタイムおよび終いラップ評価の一覧です。")
        
        perf_display_df = filtered_df_venue.copy()
        if '総合指数' in perf_display_df.columns:
            perf_display_df = perf_display_df.sort_values(by=['Ｒ', '総合指数'], ascending=[True, False]).reset_index(drop=True)
            
            def highlight_perf_only(row):
                styles = [''] * len(row)
                cols = list(row.index)
                if '総合指数' in cols:
                    idx_idx = cols.index('総合指数')
                    try:
                        val_f = float(row['総合指数'])
                        if val_f >= 100.0: styles[idx_idx] = 'background-color: #ffcccc; color: red; font-weight: bold;'
                        elif val_f >= 80.0: styles[idx_idx] = 'background-color: #fff2cc; font-weight: bold;'
                    except: pass
                
                return styles
                
            perf_cols = ['Ｒ', '馬番', '馬名', '総合指数', '長期休養フラグ', 'ラップ評価', '4Fタイム', 'Lap4', 'Lap3', 'Lap2', 'ラスト1F', 'W5F', 'Wラスト1F', 'W評価', '調教コンボ', 'レベル点', '自力点', 'ボーナス減点', '前走着順', '前走着差', '前走脚質', 'レース間隔', '好走/次走あり', '前走日付']
            perf_cols_exist = [c for c in perf_cols if c in perf_display_df.columns]
            
            col_config_integrated = {
                "Ｒ": st.column_config.NumberColumn("レース", format="%d R"), 
                "馬番": st.column_config.NumberColumn("馬番", format="%d"),
                "総合指数": st.column_config.NumberColumn("★ 総合指数", format="%.1f"), 
                "レベル点": st.column_config.NumberColumn("相手レベル点", format="%.1f 点"),
                "自力点": st.column_config.NumberColumn("自力点", format="%.1f 点"), 
                "ボーナス減点": st.column_config.NumberColumn("加減点", format="%.1f 点"),
                "4Fタイム":   st.column_config.NumberColumn("坂路4F",  format="%.1f"),
                "W5F":        st.column_config.NumberColumn("W-5F",    format="%.1f"),
                "Wラスト1F":  st.column_config.NumberColumn("Wラスト", format="%.1f"),
                "W評価":      st.column_config.TextColumn("W評価"),
                "調教コンボ": st.column_config.TextColumn("坂路×W")
            }
            
            st.dataframe(
                perf_display_df[perf_cols_exist].style.apply(highlight_perf_only, axis=1), 
                column_config=col_config_integrated, 
                use_container_width=True, 
                hide_index=True, 
                height=600
            )
        else:
            st.warning("能力比較データベースが読み込まれていないか、有効な指数データがありません。")

    elif selected_tab == "📊 本日の集計":
        st.markdown(f"### 📈 本日の成績集計 ({selected_venue})")
        st.markdown("※着順が入力されたレースのみ集計されます。")
        
        finished_races = sum(1 for r in race_list if st.session_state['saved_chaku'].get(f"c1_{selected_venue}_{r}") is not None)
        
        if finished_races > 0:
            summary_df = filtered_df_venue.copy()
            summary_df['actual_rank'] = np.nan
            
            for idx, row in summary_df.iterrows():
                r = row['Ｒ']
                num = int(row['馬番'])
                
                c1 = st.session_state['saved_chaku'].get(f"c1_{selected_venue}_{r}")
                c2 = st.session_state['saved_chaku'].get(f"c2_{selected_venue}_{r}")
                c3 = st.session_state['saved_chaku'].get(f"c3_{selected_venue}_{r}")
                
                if c1 is not None or c2 is not None or c3 is not None:
                    if num == c1: summary_df.at[idx, 'actual_rank'] = 1
                    elif num == c2: summary_df.at[idx, 'actual_rank'] = 2
                    elif num == c3: summary_df.at[idx, 'actual_rank'] = 3
                    else: summary_df.at[idx, 'actual_rank'] = 10
            
            def get_stats(mask):
                target = summary_df[mask & summary_df['actual_rank'].notnull()]
                total = len(target)
                if total == 0: 
                    return 0, 0.0, 0.0, 0.0
                wins = len(target[target['actual_rank'] == 1])
                top3s = len(target[target['actual_rank'] <= 3])
                ret = target[target['actual_rank'] == 1]['temp_odds'].sum() * 100
                return total, wins/total, top3s/total, ret/(total*100)
            
            stats_data = []
            
            mask_nerai = summary_df['騎手判定'].str.contains('〇') | summary_df['調教師判定'].str.contains('〇')
            if '馬主判定' in summary_df.columns: 
                mask_nerai |= summary_df['馬主判定'].str.contains('〇')
            t, w, p, r = get_stats(mask_nerai)
            stats_data.append({"カテゴリ": "🎯 〇 狙い目", "該当数": t, "勝率": w, "複勝率": p, "単勝回収率": r})
            
            mask_saki = summary_df['騎手判定'].str.contains('▲') | summary_df['調教師判定'].str.contains('▲')
            if '馬主判定' in summary_df.columns: 
                mask_saki |= summary_df['馬主判定'].str.contains('▲')
            t, w, p, r = get_stats(mask_saki)
            stats_data.append({"カテゴリ": "⚠️ ▲ 先買い", "該当数": t, "勝率": w, "複勝率": p, "単勝回収率": r})
            
            mask_oana = summary_df['騎手判定'].str.contains('△') | summary_df['調教師判定'].str.contains('△')
            if '馬主判定' in summary_df.columns: 
                mask_oana |= summary_df['馬主判定'].str.contains('△')
            t, w, p, r = get_stats(mask_oana)
            stats_data.append({"カテゴリ": "⚡ △ 大穴/隣ペア", "該当数": t, "勝率": w, "複勝率": p, "単勝回収率": r})
            
            mask_himo = summary_df['騎手判定'].str.contains('◆') | summary_df['調教師判定'].str.contains('◆')
            if '馬主判定' in summary_df.columns: 
                mask_himo |= summary_df['馬主判定'].str.contains('◆')
            t, w, p, r = get_stats(mask_himo)
            stats_data.append({"カテゴリ": "🔗 ◆ 紐警戒", "該当数": t, "勝率": w, "複勝率": p, "単勝回収率": r})
            
            stats_df = pd.DataFrame(stats_data)
            stats_df['勝率'] = stats_df['勝率'].apply(lambda x: f"{x:.1%}")
            stats_df['複勝率'] = stats_df['複勝率'].apply(lambda x: f"{x:.1%}")
            stats_df['単勝回収率'] = stats_df['単勝回収率'].apply(lambda x: f"{x:.0%}")
            
            st.write(f"**集計対象レース数：{finished_races} レース**")
            st.table(stats_df.set_index("カテゴリ"))
        else:
            st.info("レースの着順を入力すると、ここに本日の成績集計が表示されます。")

    st.markdown("""
    **💡 色と推奨記号のルール（PDF 6P・7Pフローチャート準拠）:**
    * <span style="background-color: #FDFFB6; padding: 2px 5px; color: black;">黄塗</span> : 連続する出走機会で配置が一致。
    * <span style="background-color: #A0C4FF; padding: 2px 5px; color: black;">青塗</span> : その日のすべての出走レースで全く同じ配置の数値に固定。
    * <span style="background-color: #A0C4FF; padding: 2px 5px; color: black; border: 1px solid blue;">馬名欄の青塗</span> : 前日「青塗」で一度も3着以内に絡めなかった騎手の「お詫び好走候補」。
    * <span style="background-color: #FFADAD; padding: 2px 5px; color: black; font-weight: bold;">〇 狙い目</span> : 青塗・ペア馬の「前走凡走後（絶好の狙い目）」。
    * <span style="background-color: #FFD6A5; padding: 2px 5px; color: black;">▲ 先買い</span> : 最初のレース（1鞍目・1回目）のため、前走の結果を待てない状態（先買いリスク）。
    * <span style="background-color: #FDFFB6; padding: 2px 5px; color: black;">△ 狙い</span> : 隣の馬にペアがある、または想定単勝オッズ50.0倍以上の大穴に該当.
    * <span style="background-color: #FDFFB6; padding: 2px 5px; color: black;">△ 後続ペア継続</span> : 前走で好走しているが、後続のレースにもペアが控えているため完全には消せない馬。
    * <span style="background-color: #CAFFBF; padding: 2px 5px; color: black; font-weight: bold;">◆ 共に凡走(紐警戒)</span> : ペア内で前走・前々走ともに凡走(4着以下)が続いている馬。紐(2・3着候補)への推奨。
    * <span style="background-color: #E2E2E2; padding: 2px 5px; color: gray;">✖ 見送り</span> : 直前のレースですでに好走（1〜3着）を終えているため、狙いから外す馬.
    * <span style="background-color: #E1BEE7; padding: 2px 5px; color: black; font-weight: bold;">🔄 对称</span> : 同一レース内で同じ調教師の馬が対称配置（正番と逆番が一致）になっている馬。
    * <span style="background-color: #F3E5F5; padding: 2px 5px; color: black;">↔️ 隣馬(対称)</span> : 对称配置になっている馬の隣の馬。
    * <span style="background-color: #E3F2FD; padding: 2px 5px; color: black;">🟦 青塗隣馬</span> : 青塗馬の隣の隣。
    """, unsafe_allow_html=True)

    # -------------------------------------------------------------------------
    # 翌日用CSVのダウンロード機能
    # -------------------------------------------------------------------------
    st.write("---")
    st.subheader("💾 翌日用結果CSVの保存")
    st.markdown("""
    各レースの「1着の馬番」「2着の馬番」「3着の馬番」に入力した結果をファイルに保存し、翌日の「前日の結果CSV」としてそのまま使用できます。
    ※当日の**全競馬場・全レース**に入力した着順が1つにまとめて反映されます（1〜3着以外は10として出力されます）。
    """)

    save_df = curr_df.copy()
    save_df['着順'] = ""

    save_df['_save_umaban_int'] = pd.to_numeric(save_df['馬番'], errors='coerce').fillna(-999).astype(int)
    save_df['_save_r_int'] = pd.to_numeric(save_df['Ｒ'], errors='coerce').fillna(-999).astype(int)

    for v in save_df['場所'].unique():
        r_list_int = sorted(save_df[save_df['場所'] == v]['_save_r_int'].unique())
        
        for r_int in r_list_int:
            if r_int == -999: 
                continue
                
            c1_val = st.session_state['saved_chaku'].get(f"c1_{v}_{r_int}")
            c2_val = st.session_state['saved_chaku'].get(f"c2_{v}_{r_int}")
            c3_val = st.session_state['saved_chaku'].get(f"c3_{v}_{r_int}")
            
            if (c1_val is not None) or (c2_val is not None) or (c3_val is not None):
                mask_race = (save_df['場所'] == v) & (save_df['_save_r_int'] == r_int)
                save_df.loc[mask_race, '着順'] = "10"
                
                if c1_val is not None: 
                    save_df.loc[mask_race & (save_df['_save_umaban_int'] == int(c1_val)), '着順'] = "1"
                if c2_val is not None: 
                    save_df.loc[mask_race & (save_df['_save_umaban_int'] == int(c2_val)), '着順'] = "2"
                if c3_val is not None: 
                    save_df.loc[mask_race & (save_df['_save_umaban_int'] == int(c3_val)), '着順'] = "3"

    save_df = save_df.drop(columns=['_save_umaban_int', '_save_r_int'])
    base_cols = ['日付S', '場所', 'Ｒ', '馬番', '馬名', '騎手', '調教師', '馬主(最新/仮想)', 'オッズ', '人気', '着順']
    save_cols = [c for c in base_cols if c in save_df.columns]
    
    csv_data = save_df[save_cols].to_csv(index=False).encode('utf-8-sig')
    
    file_prefix = curr_df['日付S'].iloc[0] if not curr_df.empty else 'output'
    st.download_button(
        label="🏆 着順入力を反映したCSVをダウンロード",
        data=csv_data,
        file_name=f"result_with_chaku_{file_prefix}.csv",
        mime="text/csv",
        use_container_width=True
    )