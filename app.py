import streamlit as st
import requests
import pandas as pd
import json
import os
import re
import pickle
import time
import io
from datetime import datetime, timedelta, date
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from atlassian import Confluence
from bs4 import BeautifulSoup

# --- КОНФИГУРАЦИЯ СТРАНИЦЫ ---
st.set_page_config(page_title="Jira Analytics Builder", layout="wide")
CONFIG_FILE = "config.json"
SNAPSHOT_FILE = "snapshot.pkl"
TEMPO_CACHE_FILE = "tempo_cache.json"
TEMPO_DEBUG_FILE = "tempo.json"
META_FILE = "jira_meta.pkl"

# Фиксированные ID полей
INDUSTRY_FIELD_ID = "customfield_36101"
COMPANY_FIELD_ID = "customfield_21501"  # Поле "Компания"

# --- CSS СТИЛИ ---
st.markdown("""
<style>
    div[data-testid="column"] {
        background-color: #f8f9fa;
        border: 1px solid #e0e0e0;
        border-radius: 8px;
        padding: 15px;
    }
    .arrow-text {
        font-size: 30px;
        color: #888;
        text-align: center;
        margin-top: 40px;
        border: none !important;
        background: none !important;
    }
    div[data-testid="stExpander"] .streamlit-expanderContent {
        padding-top: 10px;
    }
    div.row-widget.stRadio > div {
        flex-direction: row;
        justify-content: flex-start;
        border-bottom: 1px solid #ddd;
    }
</style>
""", unsafe_allow_html=True)


# --- ГЛОБАЛЬНАЯ СЕССИЯ ---
def get_session():
    if 'session' not in st.session_state:
        session = requests.Session()
        retry = Retry(connect=3, backoff_factor=0.5)
        adapter = HTTPAdapter(max_retries=retry)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        st.session_state.session = session
    return st.session_state.session


# --- ФУНКЦИИ ХРАНЕНИЯ ДАННЫХ ---
def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}


def save_config(data):
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(data, f)
        st.toast("✅ Настройки сохранены!")
    except Exception as e:
        st.error(str(e))


def save_tempo_cache(worklogs, accounts):
    try:
        with open(TEMPO_CACHE_FILE, 'w') as f:
            json.dump({"worklogs": worklogs, "accounts": accounts}, f)
    except Exception as e:
        st.error(f"Ошибка сохранения кэша Tempo: {e}")


def load_tempo_cache_data():
    if os.path.exists(TEMPO_CACHE_FILE):
        try:
            with open(TEMPO_CACHE_FILE, 'r') as f:
                data = json.load(f)
                return data.get("worklogs", []), data.get("accounts", {})
        except:
            pass
    return [], {}


def load_snapshot():
    if os.path.exists(SNAPSHOT_FILE):
        try:
            with open(SNAPSHOT_FILE, 'rb') as f:
                data = pickle.load(f)
                st.session_state.raw_issues_data = data.get('issues', [])
                st.session_state.raw_worklogs_data = data.get('worklogs', {})
                st.session_state.calculated_medians = data.get('medians', {})
                st.session_state.global_data_loaded = True
                st.session_state.snapshot_fields_map = data.get('fields_map', {})
                st.session_state.work_type_id = data.get('work_type_id', '')
                st.session_state.epic_link_id = data.get('epic_link_id', 'customfield_10300')
                st.session_state.account_id = data.get('account_id', '')
                st.session_state.is_custom_jql_snapshot = data.get('is_custom_jql', False)
                st.session_state.extra_epics_cache = data.get('extra_epics_cache', {})
                st.session_state.extra_types_cache = data.get('extra_types_cache', {})
                st.session_state.extra_parent_map = data.get('extra_parent_map', {})
                st.session_state.extra_users_cache = data.get('extra_users_cache', {})

                if 'global_names_cache' in data:
                    st.session_state.global_names_cache.update(data['global_names_cache'])
                if 'key_to_login_map' in data:
                    st.session_state.key_to_login_map.update(data['key_to_login_map'])
        except:
            pass


def load_meta():
    if os.path.exists(META_FILE):
        try:
            with open(META_FILE, 'rb') as f:
                return pickle.load(f)
        except:
            return None
    return None


def save_meta(data):
    try:
        with open(META_FILE, 'wb') as f:
            pickle.dump(data, f)
    except:
        pass


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def _json_to_html_list(data):
    """Рекурсивно превращает любой (даже неизвестный) JSON в красивые HTML-списки для Confluence."""
    if isinstance(data, dict):
        html = "<ul style='margin-top: 5px; margin-bottom: 5px; padding-left: 20px;'>"
        for k, v in data.items():
            if v in [None, "", []]: continue
            html += f"<li><strong>{k}:</strong> {_json_to_html_list(v)}</li>"
        html += "</ul>"
        return html
    elif isinstance(data, list):
        html = "<ul style='margin-top: 5px; margin-bottom: 5px; padding-left: 20px; list-style-type: circle;'>"
        for item in data:
            if item in [None, "", []]: continue
            html += f"<li>{_json_to_html_list(item)}</li>"
        html += "</ul>"
        return html
    elif isinstance(data, str):
        return str(data).replace('\n', '<br/>')
    elif data is None:
        return "<i>Нет данных</i>"
    else:
        return str(data)

def parse_dion_json(data):
    """
    Специализированный парсер для JSON структуры Dion.
    Умеет красиво раскладывать protocol.json и проверять на пустые заглушки.
    """
    html_parts = []

    # 1. Если это точный формат protocol.json
    if isinstance(data, dict) and "protocol" in data:
        proto = data.get("protocol")
        if not proto:
            return None  # Пустой протокол-заглушка

        common = proto.get("common", {})

        if common.get("name"):
            html_parts.append(f"<h3>📌 {common.get('name')}</h3>")
        if common.get("description"):
            html_parts.append(f"<p><strong>Описание:</strong> {common.get('description')}</p>")

        tags = common.get("tags", [])
        if tags:
            html_parts.append(f"<p><strong>Теги:</strong> {', '.join(tags)}</p>")

        if common.get("conclusion"):
            html_parts.append(f"<p><strong>✅ Итоги:</strong><br/> {str(common.get('conclusion')).replace(chr(10), '<br/>')}</p>")
        if common.get("plans"):
            html_parts.append(f"<p><strong>🚀 Планы:</strong><br/> {str(common.get('plans')).replace(chr(10), '<br/>')}</p>")

        content = proto.get("content", {})
        timestamps = content.get("timestamps", [])

        if timestamps:
            html_parts.append("<h4>⏱ Таймкоды / Ключевые моменты:</h4><ul>")
            for ts in timestamps:
                t_val = ts.get("time", "")
                name = ts.get("ts_name", "") or ts.get("name", "")
                if t_val and name:
                    html_parts.append(f"<li><strong>[{t_val}]</strong> {name}</li>")
            html_parts.append("</ul>")

        messages = content.get("messages", []) or content.get("replicas", [])
        if messages:
            html_parts.append("<h4>💬 Расшифровка:</h4><ul style='list-style-type: none; padding-left: 0;'>")
            for msg in messages:
                speaker = msg.get("speaker", "Участник")
                text = msg.get("text", "")
                t_val = msg.get("time", "")
                time_str = f"<span style='color: gray;'>[{t_val}]</span> " if t_val else ""
                if text:
                    html_parts.append(f"<li style='margin-bottom: 8px;'>{time_str}<strong>{speaker}:</strong> {str(text).replace(chr(10), '<br/>')}</li>")
            html_parts.append("</ul>")

    # 2. Если это просто список реплик
    elif isinstance(data, list):
        html_parts.append("<ul>")
        for item in data:
            if isinstance(item, dict):
                speaker = item.get('speaker', '') or item.get('author', '')
                text = item.get('text', '') or item.get('content', '') or item.get('message', '')
                if speaker and text:
                    html_parts.append(f"<li><b>{speaker}:</b> {str(text).replace(chr(10), '<br/>')}</li>")
                elif text:
                    html_parts.append(f"<li>{str(text).replace(chr(10), '<br/>')}</li>")
            elif isinstance(item, str):
                html_parts.append(f"<li>{str(item).replace(chr(10), '<br/>')}</li>")
        html_parts.append("</ul>")

    # 3. Если это другой JSON (например, summary.json)
    elif isinstance(data, dict):
        if "summary" in data:
            val = data["summary"]
            if isinstance(val, str):
                html_parts.append(f"<h4>Краткое содержание:</h4><p>{val.replace(chr(10), '<br/>')}</p>")
            else:
                html_parts.append(f"<h4>Краткое содержание:</h4>{_json_to_html_list(val)}")
        elif "transcript" in data:
             html_parts.append(f"<p>{_json_to_html_list(data['transcript'])}</p>")
        else:
             html_parts.append(_json_to_html_list(data))

    # Универсальный фолбэк: если совсем не поняли структуру
    if not html_parts:
        return _json_to_html_list(data)

    return "".join(html_parts)


def extract_meeting_protocol_api(api_url, token):
    """
    Извлекает протокол через API по токену авторизации (Bearer).
    Ловит заглушки сервера.
    """
    try:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }

        resp = requests.get(api_url, headers=headers, verify=False, timeout=15)

        if resp.status_code in [401, 403]:
            return False, f"Ошибка авторизации (Код {resp.status_code}). Токен недействителен или истек."

        # Если получаем 404 на конкретный файл протокола - он еще не сгенерирован
        if resp.status_code == 404:
            return False, "⚠️ В этом видео на данный момент отсутствует протокол (возможно, видео еще обрабатывается)."

        resp.raise_for_status()

        try:
            data = resp.json()
        except ValueError:
             return False, "API вернул не JSON. Проверьте правильность ссылки."

        # Парсим JSON
        text_content = parse_dion_json(data)

        # Если парсер вернул None (т.е. структура была пустой)
        if not text_content:
             return False, "⚠️ В этом видео на данный момент отсутствует протокол (сервер вернул пустые данные)."

        return True, text_content
    except Exception as e:
        return False, f"Ошибка при запросе к API: {str(e)}"


def color_budget(val):
    if val < 0.49:
        return 'background-color: #66ff66; color: black'
    elif 0.49 <= val <= 1.00:
        return 'background-color: #d9ffcc; color: black'
    elif 1.00 < val < 2.00:
        return 'background-color: #ffcccc; color: black'
    elif 2.00 <= val < 5.00:
        return 'background-color: #ff4d4d; color: black'
    else:
        return 'background-color: #800000; color: white'


def parse_tempo_date(date_val):
    if not date_val: return None
    s = str(date_val).lower().strip()
    try:
        if '-' in s: return datetime.strptime(s, '%Y-%m-%d').date()
        if '/' in s: return datetime.strptime(s, '%Y/%m/%d').date()
        if '.' in s: return datetime.strptime(s, '%d.%m.%Y').date()
    except:
        pass
    return None


def extract_period_from_excel(df_head):
    dates = []
    for idx, row in df_head.iterrows():
        for item in row:
            s_item = str(item)
            matches = re.findall(r'\d{2}\.\d{2}\.\d{4}', s_item)
            for m in matches:
                try:
                    d = datetime.strptime(m, '%d.%m.%Y').date()
                    dates.append(d)
                except:
                    pass

    if len(dates) >= 2:
        return min(dates), max(dates)
    return None, None


def check_name_match(jira_name, excel_name):
    if not jira_name or not excel_name: return False

    j_norm = str(jira_name).lower().replace('.', ' ').strip()
    e_norm = str(excel_name).lower().replace('.', ' ').strip()

    j_parts_raw = j_norm.split()
    e_parts_raw = e_norm.split()

    stop_words = {'уулу', 'оглы', 'кызы', 'uulu', 'ogly', 'kyzy'}
    j_parts = [p for p in j_parts_raw if p not in stop_words]
    e_parts = [p for p in e_parts_raw if p not in stop_words]

    if not j_parts or not e_parts: return False

    j_long = [p for p in j_parts if len(p) > 1]
    e_long = [p for p in e_parts if len(p) > 1]

    if not j_long or not e_long: return False

    common_surname = set(j_long) & set(e_long)

    if not common_surname:
        return False

    surname = list(common_surname)[0]

    j_rest = [p for p in j_parts if p != surname]
    e_rest = [p for p in e_parts if p != surname]

    if j_rest and e_rest:
        j_init = j_rest[0][0]
        e_init = e_rest[0][0]
        return j_init == e_init

    return True


def safe_extract_name(val):
    if val is None:
        return "Unknown"
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        return val.get('label') or val.get('value') or val.get('name') or str(val)
    if isinstance(val, list):
        if not val:
            return "Unknown"
        return safe_extract_name(val[0])
    return str(val)


# --- ФУНКЦИИ ДЛЯ CONFLUENCE ---

def format_links_in_df(df, columns_to_format):
    df_out = df.copy()
    for col in columns_to_format:
        if col in df_out.columns:
            def make_link(val):
                if isinstance(val, str) and val.startswith("http"):
                    key = val.rstrip('/').split('/')[-1]
                    return (
                        f'<ac:structured-macro ac:name="html">'
                        f'<ac:plain-text-body><![CDATA['
                        f'<a href="{val}" target="_blank">{key}</a>'
                        f']]></ac:plain-text-body>'
                        f'</ac:structured-macro>'
                    )
                return val
            df_out[col] = df_out[col].apply(make_link)
    return df_out

def wrap_with_chart_macro(table_html, chart_type="Pie", label_col="", value_col="", title="", height=400):
    macro_xml = f"""
    <h3>{title}</h3>
    <ac:structured-macro ac:name="table-chart" ac:schema-version="1">
      <ac:parameter ac:name="type">{chart_type}</ac:parameter>
      <ac:parameter ac:name="labelColumn">{label_col}</ac:parameter>
      <ac:parameter ac:name="valueColumn">{value_col}</ac:parameter>
      <ac:parameter ac:name="height">{height}</ac:parameter>
      <ac:parameter ac:name="width">800</ac:parameter>
      <ac:parameter ac:name="showLegend">true</ac:parameter>
      <ac:rich-text-body>
        {table_html}
      </ac:rich-text-body>
    </ac:structured-macro>
    """
    return macro_xml


def export_to_confluence_advanced(url, username, token, page_id, title, html_body, attachments=None, is_cloud=True, report_period=None):
    try:
        page_id = str(page_id).strip()
        if not page_id.isdigit():
            return False, f"Ошибка: ID страницы должен быть числом. Вы ввели: '{page_id}'"

        if is_cloud:
            confluence = Confluence(url=url, username=username, password=token, cloud=True)
        else:
            confluence = Confluence(url=url, token=token)

        try:
            page_info = confluence.get_page_by_id(page_id, expand='space,version')
        except Exception as e:
            return False, f"Не удалось найти страницу {page_id}. Ошибка: {e}"

        if not isinstance(page_info, dict):
            return False, f"API вернул некорректный ответ: {str(page_info)}"

        current_page_title = page_info.get('title', title)

        if attachments:
            for att in attachments:
                fname = att['filename']
                fdata = att['data']
                try:
                    confluence.attach_content(
                        content=fdata,
                        name=fname,
                        content_type="image/png",
                        page_id=page_id,
                        title=fname,
                        space=None,
                        comment="Uploaded via App"
                    )
                except Exception as e:
                    pass

        header_html = f"<p><em>Отчет сформирован: {datetime.now().strftime('%Y-%m-%d %H:%M')}</em></p>"
        if report_period:
            header_html += f"<p><strong>Период: {report_period}</strong></p>"

        final_html = header_html + html_body

        confluence.update_page(
            page_id=page_id,
            title=current_page_title,
            body=final_html,
            parent_id=None,
            type='page',
            representation='storage',
            minor_edit=False
        )
        return True, "Успешно экспортировано!"

    except Exception as e:
        return False, f"Ошибка Confluence: {str(e)}"


def df_to_confluence_html(df):
    if df.empty:
        return "<p>Нет данных</p>"

    with pd.option_context('display.max_colwidth', None):
        table_html = df.to_html(index=False, classes="confluenceTable", border=1, escape=False)

    table_html = table_html.replace('class="dataframe confluenceTable"', 'class="confluenceTable"')
    table_html = table_html.replace('<th>', '<th class="confluenceTh">')
    table_html = table_html.replace('<td>', '<td class="confluenceTd">')
    return table_html


config = load_config()

# --- ИНИЦИАЛИЗАЦИЯ SESSION STATE ---
if 'global_data_loaded' not in st.session_state:
    st.session_state.global_data_loaded = False
    st.session_state.raw_issues_data = []
    st.session_state.raw_worklogs_data = {}

    t_logs, t_accs = load_tempo_cache_data()
    st.session_state.tempo_raw_worklogs = t_logs
    st.session_state.tempo_accounts_map = t_accs

    st.session_state.global_names_cache = {}
    st.session_state.key_to_login_map = {}

    st.session_state.insight_cache = {}

    st.session_state.calculated_medians = {}
    st.session_state.snapshot_fields_map = {}
    st.session_state.work_type_id = ""
    st.session_state.epic_link_id = ""
    st.session_state.account_id = ""
    st.session_state.is_custom_jql_snapshot = False

    st.session_state.extra_epics_cache = {}
    st.session_state.extra_types_cache = {}
    st.session_state.extra_parent_map = {}
    st.session_state.extra_users_cache = {}
    load_snapshot()

if 'jira_meta' not in st.session_state: st.session_state.jira_meta = load_meta()
if 'tempo_teams_cache' not in st.session_state: st.session_state.tempo_teams_cache = None
if 'insight_cache' not in st.session_state: st.session_state.insight_cache = {}

# --- API HELPERS ---
def make_jira_request(url, params, method, token, json_body=None, method_type="GET"):
    headers = {"Content-Type": "application/json"}
    cookies = {}
    if method == "Personal Access Token (PAT)":
        headers["Authorization"] = f"Bearer {token}"
    else:
        cookies = {"JSESSIONID": token}

    sess = get_session()

    try:
        if method_type == "POST":
            response = sess.post(url, headers=headers, params=params, json=json_body, cookies=cookies, verify=False, timeout=120)
        else:
            response = sess.get(url, headers=headers, params=params, cookies=cookies, verify=False, timeout=120)

        if response.status_code not in [200, 201]:
            return None

        response.raise_for_status()
        return response.json()
    except Exception as e:
        return None


def get_jira_user_details(domain, username_or_key, auth_method, token):
    url = f"https://{domain}/rest/api/2/user"
    params = {}
    if str(username_or_key).upper().startswith("JIRAUSER"):
        params["key"] = username_or_key
    else:
        params["username"] = username_or_key
    return make_jira_request(url, params, auth_method, token)


def search_jira_users(domain, query, auth_method, token):
    url = f"https://{domain}/rest/api/2/user/search"
    params = {"username": query, "maxResults": 10}
    return make_jira_request(url, params, auth_method, token)


def get_all_issues(domain, jql, fields_list, method, token):
    all_issues = []
    start_at = 0
    max_results = 50
    api_url = f"https://{domain}/rest/api/2/search"
    fields_string = ",".join(fields_list)
    progress_bar = st.progress(0)
    while True:
        params = {"jql": jql, "fields": fields_string, "expand": "renderedFields", "startAt": start_at, "maxResults": max_results}
        data = make_jira_request(api_url, params, method, token)
        if not data or 'issues' not in data: break
        issues = data['issues']
        all_issues.extend(issues)
        total = data.get('total', 0)
        if total > 0: progress_bar.progress(min(len(all_issues) / total, 1.0))
        if start_at + len(issues) >= total: break
        start_at += len(issues)
    progress_bar.empty()
    return all_issues


def get_full_worklogs(domain, issue_key, method, token):
    api_url = f"https://{domain}/rest/api/2/issue/{issue_key}/worklog"
    resp = make_jira_request(api_url, {}, method, token)
    if resp and isinstance(resp, dict):
        return resp.get('worklogs', [])
    return []

# --- INSIGHT / ASSETS HELPER ---
def resolve_insight_object(domain, auth_method, token, object_key):
    if object_key in st.session_state.insight_cache:
        return st.session_state.insight_cache[object_key]

    url = f"https://{domain}/rest/insight/1.0/object/{object_key}"
    data = make_jira_request(url, {}, auth_method, token)

    if data and isinstance(data, dict):
        attributes = data.get('attributes', [])
        found_name = None
        for attr in attributes:
            attr_name = attr.get('objectTypeAttribute', {}).get('name', '').lower()
            if attr_name in ['наименование', 'name', 'title', 'название']:
                vals = attr.get('objectAttributeValues', [])
                if vals:
                    found_name = vals[0].get('displayValue') or vals[0].get('value')
                break

        if not found_name:
            found_name = data.get('label') or data.get('objectKey')

        if found_name:
            st.session_state.insight_cache[object_key] = found_name
            return found_name

    return object_key


# --- TEMPO SPECIFIC FETCHERS ---
def fetch_tempo_raw_data(domain, start_date, end_date, method, token, status_placeholder, project_keys=None, worker_ids=None):
    api_url = f"https://{domain}/rest/tempo-timesheets/4/worklogs/search"
    all_worklogs = []
    limit = 1000
    offset = 0
    page_count = 0
    max_pages = 200

    st.info(f"Начинаем загрузку Tempo... (Страницами по {limit})")

    payload = {
        "from": start_date.strftime("%Y-%m-%d"),
        "to": end_date.strftime("%Y-%m-%d")
    }
    if project_keys:
        payload["projectKey"] = project_keys

    if worker_ids and len(worker_ids) > 0:
        payload["worker"] = worker_ids

    while page_count < max_pages:
        if len(all_worklogs) >= 25000:
            st.warning("🛑 Достигнут лимит 25,000 записей. Остановка.")
            break

        params = {"limit": limit, "offset": offset}
        status_placeholder.text(f"⏳ Загрузка страницы {page_count + 1}... (Загружено: {len(all_worklogs)})")

        data = make_jira_request(api_url, params, method, token, json_body=payload, method_type="POST")
        if data is None: break

        chunk = []
        if isinstance(data, list): chunk = data
        elif isinstance(data, dict): chunk = data.get('results', [])

        if not chunk: break
        all_worklogs.extend(chunk)
        offset += len(chunk)
        page_count += 1
        if len(chunk) < limit: break

    status_placeholder.text(f"✅ Готово! Всего загружено: {len(all_worklogs)}")
    return all_worklogs


def fetch_tempo_accounts(domain, method, token):
    api_url = f"https://{domain}/rest/tempo-accounts/1/account"
    st.info("Скачивание справочника аккаунтов...")
    data = make_jira_request(api_url, {}, method, token)
    acc_map = {}
    if data:
        for acc in data:
            k = acc.get('key')
            n = acc.get('name')
            if k: acc_map[k] = n
    return acc_map


def resolve_users_bulk(domain, method, token, user_ids):
    cache = st.session_state.global_names_cache
    map_cache = st.session_state.key_to_login_map

    missing = []
    for uid in user_ids:
        if uid and uid not in cache:
            missing.append(uid)

    missing = list(set(missing))

    if not missing: return

    st.info(f"🔎 Расшифровка имен для {len(missing)} пользователей...")
    progress_bar = st.progress(0)

    for i, uid in enumerate(missing):
        u_data = get_jira_user_details(domain, uid, method, token)

        if u_data:
            d_name = u_data.get('displayName')
            key = u_data.get('key')
            login = u_data.get('name')

            if key: cache[key] = d_name
            if login: cache[login] = d_name
            cache[uid] = d_name

            if key and login:
                map_cache[key] = login
                map_cache[key.lower()] = login
        else:
            cache[uid] = uid

        progress_bar.progress((i + 1) / len(missing))

    progress_bar.empty()
    st.session_state.global_names_cache = cache
    st.session_state.key_to_login_map = map_cache


# --- HELPERS ---
meta_projects = {}
meta_fields = {}
meta_fields_inv = {}

if st.session_state.jira_meta:
    meta_projects = {p['key']: p['name'] for p in st.session_state.jira_meta.get('projects', [])}
    meta_fields = {f['id']: f['name'] for f in st.session_state.jira_meta.get('fields', [])}
    meta_fields_inv = {v: k for k, v in meta_fields.items()}

# === SIDEBAR (НАСТРОЙКИ) ===
with st.sidebar:
    st.header("⚙️ Подключение")
    jira_domain = st.text_input("Jira Domain", value=config.get("domain", "jira.company.com"))
    auth_idx = config.get("auth_index", 0)
    auth_method = st.radio("Авторизация", ["Personal Access Token (PAT)", "Browser Cookie (JSESSIONID)"], index=auth_idx)
    api_token = st.text_input("Token / Cookie", value=config.get("token", ""), type="password")

    if st.button("🔄 Обновить метаданные"):
        if not api_token:
            st.error("Нет токена")
        else:
            with st.spinner("Загрузка..."):
                projs = make_jira_request(f"https://{jira_domain}/rest/api/2/project", {}, auth_method, api_token)
                flds = make_jira_request(f"https://{jira_domain}/rest/api/2/field", {}, auth_method, api_token)
                if projs and flds:
                    st.session_state.jira_meta = {"projects": projs, "fields": flds}
                    save_meta(st.session_state.jira_meta)
                    st.success("OK")
                    st.rerun()
                else:
                    st.error("Ошибка")

    st.markdown("---")

    with st.expander("📤 Экспорт в Confluence"):
        conf_type = st.radio("Тип Confluence", ["Cloud (Email + API Token)", "Server / Data Center (PAT)"],
                             index=0 if config.get("conf_type") == "Cloud" else 1)
        conf_url = st.text_input("Confluence URL", value=config.get("conf_url", f"https://{jira_domain}/wiki"))

        conf_user = ""
        if "Cloud" in conf_type:
            conf_user = st.text_input("Confluence User (Email)", value=config.get("conf_user", ""))

        conf_token = st.text_input("Token / PAT", value=config.get("conf_token", ""), type="password")

        if st.button("💾 Сохранить настройки Confluence"):
            cur_conf = load_config()
            cur_conf.update({
                "conf_type": "Cloud" if "Cloud" in conf_type else "Server",
                "conf_url": conf_url,
                "conf_user": conf_user,
                "conf_token": conf_token
            })
            save_config(cur_conf)

    st.markdown("---")
    st.header("🔧 Настройки полей")

    sel_wt_id = config.get("field_id", "customfield_22202")

    if st.session_state.jira_meta:
        field_opts = sorted([f"{name}" for id, name in meta_fields.items()])
        def_f_name = meta_fields.get(sel_wt_id, "")
        try:
            idx = field_opts.index(def_f_name)
        except:
            idx = 0
        sel_wt_name = st.selectbox("Поле 'Вид работ'", options=field_opts, index=idx)
        sel_wt_id = meta_fields_inv.get(sel_wt_name)
    else:
        st.warning("Загрузите метаданные")
        sel_wt_id = st.text_input("ID поля 'Вид работ' (вручную)", value=sel_wt_id)

    st.markdown("---")

    with st.expander("🔎 Найти ID пользователя"):
        user_search_query = st.text_input("Фамилия, логин или JIRAUSER:", key="user_finder")
        if st.button("Найти") and user_search_query:
            if not api_token:
                st.error("Нет токена")
            else:
                found_list = []
                seen_keys = set()

                direct_user = get_jira_user_details(jira_domain, user_search_query.strip(), auth_method, api_token)
                if direct_user and 'name' in direct_user:
                    found_list.append(direct_user)
                    seen_keys.add(direct_user.get('key'))

                fuzzy_users = search_jira_users(jira_domain, user_search_query, auth_method, api_token)
                if fuzzy_users:
                    for u in fuzzy_users:
                        if u.get('key') not in seen_keys:
                            found_list.append(u)
                            seen_keys.add(u.get('key'))

                if found_list:
                    for u in found_list:
                        st.markdown(f"**{u.get('displayName')}**")
                        st.code(f"Name: {u.get('displayName')}\nKey: {u.get('key')}\nLogin: {u.get('name')}",
                                language="yaml")
                else:
                    st.warning("Не найдено")

    with st.expander("👥 Tempo Команды (Конфиг)"):
        def fetch_tempo_teams_history(domain, method, token):
            url_base = f"https://{domain}/rest/tempo-teams/2/team"
            headers = {"Content-Type": "application/json"}
            cookies = {}
            if method == "Personal Access Token (PAT)":
                headers["Authorization"] = f"Bearer {token}"
            else:
                cookies = {"JSESSIONID": token}

            try:
                resp = requests.get(url_base, headers=headers, cookies=cookies, verify=False)
                if resp.status_code != 200:
                    st.error(f"Ошибка получения команд: {resp.status_code}")
                    return None

                teams = resp.json()
                result_map = {}

                prog = st.progress(0)
                status_text = st.empty()

                for idx, team in enumerate(teams):
                    tid = team.get('id')
                    tname = team.get('name')
                    status_text.text(f"Сканируем команду: {tname}...")

                    m_url = f"{url_base}/{tid}/member"
                    m_resp = requests.get(m_url, headers=headers, cookies=cookies, verify=False)

                    members_hist = []
                    if m_resp.status_code == 200:
                        raw_members = m_resp.json()

                        for m in raw_members:
                            mem_obj = m.get('membership', {})
                            raw_from = mem_obj.get('dateFromANSI')
                            raw_to = mem_obj.get('dateToANSI')

                            if not raw_from: raw_from = mem_obj.get('dateFrom') or m.get('dateFrom')
                            if not raw_to: raw_to = mem_obj.get('dateTo') or m.get('dateTo')

                            d_from = date(2000, 1, 1)
                            if raw_from and str(raw_from).strip():
                                parsed = parse_tempo_date(raw_from)
                                if parsed: d_from = parsed

                            d_to = date(2099, 12, 31)
                            if raw_to and str(raw_to).strip():
                                parsed = parse_tempo_date(raw_to)
                                if parsed: d_to = parsed

                            m_info = m.get('member', {})
                            login = m_info.get('name')
                            key = m_info.get('key')
                            real_name = m_info.get('displayName')

                            if not real_name:
                                real_name = login

                            lookup_id = key if key else login
                            if lookup_id and (not real_name or real_name == login):
                                try:
                                    u_d = get_jira_user_details(domain, lookup_id, method, token)
                                    if u_d and 'displayName' in u_d:
                                        real_name = u_d['displayName']
                                except:
                                    pass

                            if not real_name:
                                real_name = str(login) if login else "Unknown"

                            if login:
                                members_hist.append({
                                    "login": login,
                                    "key": key,
                                    "name": real_name,
                                    "dateFrom": str(d_from),
                                    "dateTo": str(d_to)
                                })
                                st.session_state.global_names_cache[login] = real_name
                                if key:
                                    st.session_state.key_to_login_map[key] = login

                    if members_hist:
                        result_map[tname] = members_hist

                    prog.progress((idx + 1) / len(teams))

                prog.empty()
                status_text.empty()
                return result_map

            except Exception as e:
                st.error(f"Критическая ошибка при загрузке команд: {e}")
                return None


        if st.button("📥 Скачать структуру (Tempo) + Имена"):
            if not api_token:
                st.error("Нет токена")
            else:
                with st.spinner("Скачиваем команды..."):
                    teams_data = fetch_tempo_teams_history(jira_domain, auth_method, api_token)
                    if teams_data:
                        st.session_state.tempo_teams_cache = teams_data
                        st.success("Загружено! Теперь сохраните конфиг.")

        teams_val = st.session_state.tempo_teams_cache or config.get("teams", {})
        teams_json_str = st.text_area("JSON", value=json.dumps(teams_val, indent=2, ensure_ascii=False), height=100)

    st.write("---")
    if st.button("💾 Сохранить общие настройки"):
        try:
            t_obj = json.loads(teams_json_str)
            save_config({
                "domain": jira_domain,
                "auth_index": 0 if "Personal Access Token" in auth_method else 1,
                "token": api_token,
                "project": config.get("project", ["PRESALE"]),
                "field_id": sel_wt_id,
                "epic_link_id": "customfield_10300",
                "teams": t_obj
            })
        except Exception as e:
            st.error(str(e))

# === MAIN UI: BUILDER ===
if not st.session_state.jira_meta:
    st.info("👋 Добро пожаловать! Для начала нажмите **'🔄 Обновить метаданные'** в меню слева.")
    st.stop()


# --- СОЗДАЕМ ЗАКЛАДКИ НА ВЕРХНЕМ УРОВНЕ ---
tab_jira, tab_protocols = st.tabs(["📊 Аналитика Jira", "📝 Протоколы встреч"])

# ==========================================
# ВКЛАДКА 2: ПРОТОКОЛЫ ВСТРЕЧ
# ==========================================
with tab_protocols:
    st.markdown("### 📥 Импорт протокола встречи (API Dion)")
    st.info("Укажите ссылку на видео и токен авторизации. ID видео будет определен автоматически.")

    c_proto1, c_proto2 = st.columns(2)

    with c_proto1:
        video_url = st.text_input("Ссылка на страницу с видео", placeholder="https://dion.vc/watch/...")
        conf_page_id = st.text_input("Номер (ID) страницы Confluence", placeholder="Например: 123456789")

    with c_proto2:
        dion_token = st.text_input("Dion Token (Bearer)", type="password", placeholder="ey...")

    with st.expander("🛠 Продвинутые настройки (Другой API URL)", expanded=False):
        custom_api = st.text_input("Свой API URL (Например, прямая ссылка на summary.json)", help="Оставьте пустым для автоматической генерации protocol.json")

    if st.button("Скачать и опубликовать", type="primary"):
        if not video_url or not dion_token or not conf_page_id:
            st.warning("Пожалуйста, заполните ссылку на видео, Token и ID страницы Confluence.")
        else:
            if custom_api.strip():
                api_url = custom_api.strip()
            else:
                clean_url = video_url.split('?')[0].strip('/')
                video_id = clean_url.split('/')[-1]
                api_url = f"https://hls-delivery.dion.vc/v1/records/{video_id}/protocol.json?protocol_type_code=6"

            with st.spinner("Загрузка JSON протокола из API..."):
                success, protocol_text = extract_meeting_protocol_api(api_url, dion_token)

                if success:
                    st.success("✅ Протокол успешно получен и обработан!")
                    with st.spinner("Публикация в Confluence..."):
                        c_url = config.get("conf_url")
                        c_user = config.get("conf_user")
                        c_token = config.get("conf_token")
                        c_type = config.get("conf_type", "Cloud")
                        is_cloud = (c_type == "Cloud")

                        if not (c_url and c_token):
                            st.error("Настройки подключения Confluence (слева в меню) не заполнены!")
                        else:
                            source_link = f"<p><strong>Источник:</strong> <a href='{video_url}'>{video_url}</a></p>" if video_url else ""

                            html_body = f"""
                            <h2>Протокол встречи</h2>
                            {source_link}
                            <hr/>
                            <div style="padding-top: 15px;">
                                {protocol_text}
                            </div>
                            """

                            res, msg = export_to_confluence_advanced(
                                c_url, c_user, c_token, conf_page_id, "Протокол встречи",
                                html_body, attachments=[], is_cloud=is_cloud
                            )
                            if res:
                                base_conf_url = c_url.rstrip('/')
                                page_link = f"{base_conf_url}/pages/viewpage.action?pageId={conf_page_id}"
                                st.success(f"✅ Данные успешно опубликованы! Открыть страницу [{conf_page_id}]({page_link})")
                            else:
                                st.error(f"❌ Ошибка публикации: {msg}")
                else:
                    st.warning(protocol_text)


# ==========================================
# ВКЛАДКА 1: АНАЛИТИКА JIRA (Основной функционал)
# ==========================================
with tab_jira:
    # --- КОНСТРУКТОР ---
    with st.expander("🛠 Настройка Запроса (Развернуть/Свернуть)", expanded=True):
        custom_jql_text = ""
        c1, a1, c2, a2, c3, a3, c4 = st.columns([1.2, 0.2, 1, 0.2, 1.2, 0.2, 1])

        with c1:
            st.markdown("#### 1. Источник")
            sel_projects = st.multiselect("Проекты", options=sorted(list(meta_projects.keys())),
                                          default=config.get("project", ["PRESALE"]))
        with a1:
            st.markdown('<div class="arrow-text">➜</div>', unsafe_allow_html=True)
        with c2:
            st.markdown("#### 2. База (Период)")
            st.caption("Период для поиска задач")
            d_start = st.date_input("С", value=date(date.today().year, 1, 1))
            d_end = st.date_input("По", value=date.today())
        with a2:
            st.markdown('<div class="arrow-text">➜</div>', unsafe_allow_html=True)
        with c3:
            st.markdown("#### 3. Столбцы")
            st.info(f"Вид работ: **{meta_fields.get(sel_wt_id, sel_wt_id)}**")
            field_names_sorted = sorted([f"{name}" for id, name in meta_fields.items()])
            sel_epic_link_id = "customfield_10300"
            sel_account_id = ""
            for fid, fname in meta_fields.items():
                fn_low = fname.lower()
                if "epic link" in fn_low or "ссылка на эпик" in fn_low: sel_epic_link_id = fid
                if "account" in fn_low: sel_account_id = fid

            default_cols = ["Автор", "Исполнитель", "Статус"]
            safe_defaults = [d for d in default_cols if d in field_names_sorted]
            sel_extra = st.multiselect("Доп. поля таблицы", options=field_names_sorted, default=safe_defaults)
            sel_extra_ids = [meta_fields_inv.get(n) for n in sel_extra if n in meta_fields_inv]
        with a3:
            st.markdown('<div class="arrow-text">➜</div>', unsafe_allow_html=True)
        with c4:
            st.markdown("#### 4. Запуск")
            st.write("")
            run_btn = st.button("🚀 ЗАГРУЗИТЬ БАЗУ", type="primary", use_container_width=True)
            use_custom_jql = st.checkbox("Свой JQL запрос", value=False)
            show_jql = st.checkbox("Показать JQL", value=False)
            force = st.checkbox("Принудительно", value=False)

        if use_custom_jql:
            st.markdown("---")
            custom_jql_text = st.text_area("Введите JQL запрос:", height=100, help="Пример: project = INT AND updated > -30d")

    # --- ЛОГИКА ЗАПУСКА ---
    if run_btn:
        if not api_token:
            st.error("Нет токена")
        elif not sel_projects and not use_custom_jql:
            st.error("Выберите проект или введите JQL")
        else:
            if use_custom_jql:
                jql = custom_jql_text
            else:
                s_str = d_start.strftime('%Y-%m-%d')
                e_str = d_end.strftime('%Y-%m-%d')
                quoted_projects = [f'"{p}"' for p in sel_projects]
                proj_str = ", ".join(quoted_projects)
                jql = f"project in ({proj_str}) AND timespent > 0 AND worklogDate >= '{s_str}' AND worklogDate <= '{e_str}'"

            if show_jql:
                st.info("🔍 Итоговый JQL:")
                st.code(jql, language="sql")

            base_f = ['key', 'summary', 'timespent', 'status', 'creator', 'assignee', 'labels', 'issuetype', 'issuelinks', sel_wt_id, sel_epic_link_id, INDUSTRY_FIELD_ID, COMPANY_FIELD_ID]
            if sel_account_id: base_f.append(sel_account_id)
            final_f = list(set(base_f + sel_extra_ids))

            need_load = force or not st.session_state.global_data_loaded

            if need_load:
                with st.status("Выполнение...", expanded=True) as status:
                    st.write("🔍 Поиск задач...")
                    issues = get_all_issues(jira_domain, jql, final_f, auth_method, api_token)

                    if issues:
                        st.write(f"📦 Задач: {len(issues)}. Анализ медиан...")
                        d_m = []
                        for i in issues:
                            raw = i['fields'].get(sel_wt_id)
                            wt = "Unknown"
                            if isinstance(raw, dict):
                                wt = raw.get('value', 'Unknown')
                            elif raw:
                                wt = str(raw)
                            d_m.append({"Type": wt, "Hours": (i['fields'].get('timespent') or 0) / 3600})

                        medians = {}
                        if d_m: medians = pd.DataFrame(d_m).groupby("Type")["Hours"].median().to_dict()

                        st.write("📥 Скачивание ворклогов...")
                        wl_cache = {}
                        bar = st.progress(0)
                        for i, iss in enumerate(issues):
                            k = iss['key']
                            wl_cache[k] = get_full_worklogs(jira_domain, k, auth_method, api_token)
                            bar.progress((i + 1) / len(issues))
                        bar.empty()

                        f_map = {fid: meta_fields.get(fid, fid) for fid in final_f}

                        extra_epics_map = {}
                        extra_types_map = {}
                        extra_parent_map = {}
                        extra_users_map = {}
                        referenced_epics = set()
                        loaded_keys = {i['key'] for i in issues}

                        need_portfolio_hierarchy = ("PROJECT" in sel_projects) or use_custom_jql

                        for i in issues:
                            elink = i['fields'].get(sel_epic_link_id)
                            if elink: referenced_epics.add(elink)

                            if need_portfolio_hierarchy:
                                for link in i['fields'].get('issuelinks', []):
                                    l_type = link.get('type', {})
                                    name_str = (l_type.get('name', '') + l_type.get('inward', '') + l_type.get('outward', '')).lower()
                                    if 'декомпозирована' in name_str:
                                        target = link.get('outwardIssue') or link.get('inwardIssue')
                                        if target: referenced_epics.add(target.get('key'))

                        missing_epics = list(referenced_epics - loaded_keys)

                        depth = 0
                        max_depth = 3 if need_portfolio_hierarchy else 1

                        while missing_epics and depth < max_depth:
                            depth += 1

                            if need_portfolio_hierarchy:
                                st.write(f"⏳ Догружаем {len(missing_epics)} связанных эпиков/портфелей (Итерация {depth})...")
                            else:
                                st.write(f"⏳ Догружаем {len(missing_epics)} родительских эпиков...")

                            chunk_size = 50
                            new_discovered = set()

                            for i in range(0, len(missing_epics), chunk_size):
                                chunk = missing_epics[i:i + chunk_size]
                                ep_data = get_all_issues(jira_domain, f"key in ({','.join(chunk)})", ["summary", "issuetype", "issuelinks", "creator", "assignee"], auth_method, api_token)
                                if ep_data:
                                    for ep in ep_data:
                                        ek = ep['key']
                                        loaded_keys.add(ek)
                                        extra_epics_map[ek] = ep['fields'].get('summary', '')
                                        extra_types_map[ek] = ep['fields'].get('issuetype', {}).get('name', 'Unknown')

                                        cr = ep['fields'].get('creator', {}).get('displayName', 'Unknown')
                                        assig = ep['fields'].get('assignee')
                                        assig_name = assig.get('displayName', 'Unassigned') if assig else 'Unassigned'
                                        extra_users_map[ek] = {"creator": cr, "assignee": assig_name}

                                        if need_portfolio_hierarchy:
                                            for link in ep['fields'].get('issuelinks', []):
                                                l_type = link.get('type', {})
                                                name_str = (l_type.get('name', '') + l_type.get('inward', '') + l_type.get('outward', '')).lower()
                                                if 'декомпозирована' in name_str:
                                                    target = link.get('outwardIssue') or link.get('inwardIssue')
                                                    if target:
                                                        t_key = target.get('key')
                                                        extra_parent_map[ek] = t_key
                                                        if t_key not in loaded_keys:
                                                            new_discovered.add(t_key)
                            missing_epics = list(new_discovered)

                        st.session_state.raw_issues_data = issues
                        st.session_state.raw_worklogs_data = wl_cache
                        st.session_state.calculated_medians = medians
                        st.session_state.snapshot_fields_map = f_map
                        st.session_state.work_type_id = sel_wt_id
                        st.session_state.epic_link_id = sel_epic_link_id
                        st.session_state.account_id = sel_account_id
                        st.session_state.is_custom_jql_snapshot = use_custom_jql

                        st.session_state.extra_epics_cache = extra_epics_map
                        st.session_state.extra_types_cache = extra_types_map
                        st.session_state.extra_parent_map = extra_parent_map
                        st.session_state.extra_users_cache = extra_users_map

                        st.session_state.tempo_raw_worklogs = []
                        st.session_state.tempo_accounts_map = {}
                        t_logs, t_accs = load_tempo_cache_data()
                        st.session_state.tempo_raw_worklogs = t_logs
                        st.session_state.tempo_accounts_map = t_accs

                        st.session_state.global_data_loaded = True

                        with open(SNAPSHOT_FILE, 'wb') as f:
                            pickle.dump({
                                "issues": issues, "worklogs": wl_cache, "medians": medians,
                                "fields_map": f_map, "work_type_id": sel_wt_id,
                                "epic_link_id": sel_epic_link_id, "account_id": sel_account_id,
                                "is_custom_jql": use_custom_jql,
                                "extra_epics_cache": extra_epics_map,
                                "extra_types_cache": extra_types_map,
                                "extra_parent_map": extra_parent_map,
                                "extra_users_cache": extra_users_map,
                                "global_names_cache": st.session_state.global_names_cache,
                                "key_to_login_map": st.session_state.key_to_login_map
                            }, f)

                        status.update(label=f"✅ Успешно! Загружено задач: {len(issues)}", state="complete", expanded=False)
                    else:
                        status.update(label="❌ Пусто", state="error")
                        st.warning("Задач не найдено.")
                        st.stop()

    st.divider()

    # === ANALYTICS VIEW ===
    if st.session_state.global_data_loaded:
        with st.expander("📊 Аналитика (Мгновенный фильтр)", expanded=True):
            try:
                teams_conf = json.loads(teams_json_str)
            except:
                teams_conf = {}
            user_hist = {}

            # --- БЛОК 1: Формирование истории (для Аналитики) ---
            for tname, mems in teams_conf.items():
                for m in mems:
                    if isinstance(m, dict):
                        l = m.get('login', '').strip()
                        l_low = l.lower()
                        dn = m.get('name', '')
                        if l:
                            if dn:
                                st.session_state.global_names_cache[l] = dn
                                st.session_state.global_names_cache[l_low] = dn

                            try:
                                raw_start = m.get('dateFrom')
                                if raw_start and str(raw_start) not in ['None', '']:
                                    sd = datetime.strptime(str(raw_start)[:10], '%Y-%m-%d').date()
                                else:
                                    sd = date(2000, 1, 1)

                                raw_end = m.get('dateTo')
                                if raw_end and str(raw_end) not in ['None', '']:
                                    ed = datetime.strptime(str(raw_end)[:10], '%Y-%m-%d').date()
                                else:
                                    ed = date(2099, 12, 31)
                            except:
                                sd = date(2000, 1, 1)
                                ed = date(2099, 12, 31)

                            if l_low not in user_hist: user_hist[l_low] = []
                            user_hist[l_low].append({"team": tname, "start": sd, "end": ed})

            col_an1, col_an2 = st.columns(2)
            with col_an1:
                if st.session_state.get('is_custom_jql_snapshot', False):
                    default_analytics_range = (date(2000, 1, 1), date.today())
                else:
                    default_analytics_range = (d_start, d_end)
                an_range = st.date_input("Период отображения ворклогов", default_analytics_range)

        if not an_range:
            st.stop()

        s_date = an_range[0]
        e_date = an_range[1] if len(an_range) > 1 else an_range[0]
        period_str = f"{s_date.strftime('%Y/%m/%d')} – {e_date.strftime('%Y/%m/%d')}"
        s_ds, e_ds = s_date.strftime('%Y-%m-%d'), e_date.strftime('%Y-%m-%d')

        all_iss = st.session_state.raw_issues_data
        all_logs = st.session_state.raw_worklogs_data
        medians = st.session_state.calculated_medians
        f_map = st.session_state.snapshot_fields_map
        wt_id = st.session_state.work_type_id
        epic_link_id = st.session_state.epic_link_id
        account_id = st.session_state.account_id
        extra_epics_cache = st.session_state.extra_epics_cache

        gl_projects = sorted(list(set(i['key'].split('-')[0] for i in all_iss)))

        gl_teams = set()
        for t_name in teams_conf.keys(): gl_teams.add(t_name)
        gl_teams.add("Без команды")
        gl_teams = sorted(list(gl_teams))

        raw_names = [str(n) for n in st.session_state.global_names_cache.values() if n]
        gl_users = sorted(list(set(raw_names)))

        gl_inds = set()
        for i in all_iss:
            raw_ind = i['fields'].get(INDUSTRY_FIELD_ID)
            val = "Не указано"
            if isinstance(raw_ind, dict):
                val = raw_ind.get('value', 'Не указано')
            elif raw_ind:
                val = str(raw_ind)
            gl_inds.add(val)
        gl_inds = sorted(list(gl_inds))

        # --- DATA PREPARATION ---
        rows_iss = []
        wl_aggregator = {}
        epic_aggregator = {}

        for iss in all_iss:
            k = iss['key']
            f = iss['fields']

            company_val_raw = f.get(COMPANY_FIELD_ID)
            company_name = "Unknown Company"

            if isinstance(company_val_raw, dict):
                company_name = safe_extract_name(company_val_raw)
            elif isinstance(company_val_raw, list) and company_val_raw:
                raw_str = str(company_val_raw[0])
                match = re.search(r'\((CLIBD-\d+)\)', raw_str)
                if match:
                    ins_key = match.group(1)
                    company_name = resolve_insight_object(jira_domain, auth_method, api_token, ins_key)
                else:
                    company_name = safe_extract_name(company_val_raw)
            else:
                company_name = safe_extract_name(company_val_raw)

            logs = all_logs.get(k, [])

            raw_wt = f.get(wt_id)
            wt = "Unknown"
            if isinstance(raw_wt, dict):
                wt = raw_wt.get('value', 'Unknown')
            elif raw_wt:
                wt = str(raw_wt)

            issue_type = f.get('issuetype', {}).get('name', '')
            issue_epic_link = f.get(epic_link_id)
            issue_summary = f.get('summary', '')

            industry_raw = f.get(INDUSTRY_FIELD_ID)
            issue_industry = "Не указано"
            if isinstance(industry_raw, dict):
                issue_industry = industry_raw.get('value', 'Не указано')
            elif industry_raw:
                issue_industry = str(industry_raw)

            has_act = False
            issue_involved_teams = set()
            issue_involved_users = set()

            if logs and isinstance(logs, list):
                for l in logs:
                    ls = l.get('started', '')[:10]
                    if s_ds <= ls <= e_ds:
                        has_act = True
                        auth = l.get('author', {})
                        login = auth.get('name', 'u').strip().lower()
                        dname = st.session_state.global_names_cache.get(login, auth.get('displayName', login))

                        sec = l.get('timeSpentSeconds', 0)

                        curr_teams = set()
                        if login in user_hist:
                            for rec in user_hist[login]:
                                ld = datetime.strptime(ls, '%Y-%m-%d').date()
                                if rec['start'] <= ld <= rec['end']:
                                    curr_teams.add(rec['team'])

                        issue_involved_users.add(dname)
                        if curr_teams:
                            issue_involved_teams.update(curr_teams)
                        else:
                            issue_involved_teams.add("Без команды")

                        agg_key = (k, login)
                        if agg_key not in wl_aggregator:
                            wl_aggregator[agg_key] = {
                                "teams_set": set(), "name": dname, "seconds": 0, "issue_key": k,
                                "issue_link": f"https://{jira_domain}/browse/{k}",
                                "summary": issue_summary, "work_type": wt, "industry": issue_industry,
                                "company_name": company_name
                            }
                        wl_aggregator[agg_key]["seconds"] += sec
                        wl_aggregator[agg_key]["teams_set"].update(curr_teams)

                        target_epic_key = None
                        target_epic_summary = "—"
                        if issue_type in ['Epic', 'Эпик']:
                            target_epic_key = k
                            target_epic_summary = issue_summary
                        elif issue_epic_link:
                            target_epic_key = issue_epic_link
                            ep_obj = next((i for i in all_iss if i['key'] == target_epic_key), None)
                            if ep_obj:
                                target_epic_summary = ep_obj['fields'].get('summary', '')
                            else:
                                target_epic_summary = extra_epics_cache.get(target_epic_key, f"Epic {target_epic_key}")

                        if target_epic_key:
                            ep_agg_key = (target_epic_key, target_epic_summary, k, issue_summary, dname,
                                          ", ".join(sorted(list(curr_teams))) if curr_teams else "Без команды")
                            if ep_agg_key not in epic_aggregator:
                                epic_aggregator[ep_agg_key] = {
                                    "teams_list": list(curr_teams), "seconds": 0, "industry": issue_industry
                                }
                            epic_aggregator[ep_agg_key]["seconds"] += sec

            if has_act:
                sp = round((f.get('timespent', 0) or 0) / 3600, 2)
                med = medians.get(wt, 0)
                coef = (sp / med) if med > 0 else 0
                row = {
                    "Задача": f"https://{jira_domain}/browse/{k}", "Тема": issue_summary, "Вид работ": wt,
                    "Отрасль": issue_industry,
                    "Факт": sp, "Медиана": med, "Коэф.": coef,
                    "_project_key": k.split('-')[0],
                    "_involved_teams": list(issue_involved_teams),
                    "_involved_users": list(issue_involved_users),
                    "_industry": issue_industry
                }
                for fid in sel_extra_ids:
                    fname = f_map.get(fid, fid)
                    val = f.get(fid)
                    final_val = "—"
                    if isinstance(val, dict):
                        if 'name' in val:
                            u_login = val.get('name').lower().strip()
                            final_val = st.session_state.global_names_cache.get(u_login,
                                                                                 val.get('displayName', val.get('name')))
                        elif 'value' in val:
                            final_val = val.get('value')
                        else:
                            final_val = str(val)
                    elif isinstance(val, list):
                        vals_str = []
                        for v in val:
                            if isinstance(v, dict):
                                vals_str.append(v.get('value') or v.get('name') or str(v))
                            else:
                                vals_str.append(str(v))
                        final_val = ", ".join(vals_str)
                    elif val is not None:
                        final_val = str(val)
                    row[fname] = final_val
                rows_iss.append(row)

        rows_wl = []
        for key, data in wl_aggregator.items():
            t_list = sorted(list(data["teams_set"]))
            rows_wl.append({
                "Команды": ", ".join(t_list) if t_list else "Без команды", "TeamsList": t_list, "Сотрудник": data["name"],
                "Задача": data["issue_link"], "Тема": data["summary"], "Вид работ": data["work_type"],
                "Отрасль": data["industry"],
                "Списано (ч)": round(data["seconds"] / 3600, 2), "_project_key": data["issue_key"].split('-')[0],
                "_industry": data["industry"]
            })

        epics_flat_rows = []
        for key_tuple, data in epic_aggregator.items():
            epics_flat_rows.append({
                "Эпик": f"https://{jira_domain}/browse/{key_tuple[0]}", "Эпик Название": key_tuple[1],
                "Сотрудник": key_tuple[4], "Команды": key_tuple[5], "TeamsList": data["teams_list"],
                "Задача": f"https://{jira_domain}/browse/{key_tuple[2]}", "Тема": key_tuple[3],
                "Отрасль": data["industry"],
                "Списано (ч)": round(data["seconds"] / 3600, 2), "_project_key": key_tuple[2].split('-')[0],
                "_industry": data["industry"]
            })

        # --- TABS ---
        if "main_tabs_radio" not in st.session_state:
            st.session_state.main_tabs_radio = "📋 Задачи"

        tabs_list = ["📋 Задачи", "🚀 Эпики", "👥 Детализация", "✅ QC Темпо", "📂 Табель"]

        if "PROJECT" in gl_projects:
            tabs_list.insert(2, "💼 Портфели")

        selected_tab = st.radio("Раздел", tabs_list, horizontal=True, key="main_tabs_radio")

        if selected_tab == "📋 Задачи":
            df_i = pd.DataFrame(rows_iss) if rows_iss else pd.DataFrame()

            t1_c1, t1_c2, t1_c3, t1_c4 = st.columns(4)

            sel_proj = t1_c1.multiselect("Проект", gl_projects, key="f1_proj")
            nor_proj = t1_c1.checkbox("NOR (исключить)", key="n1_proj")

            sel_ind = t1_c2.multiselect("Отрасль", gl_inds, key="f1_ind")
            nor_ind = t1_c2.checkbox("NOR (исключить)", key="n1_ind")

            avail_teams_1 = gl_teams
            if sel_proj and not df_i.empty:
                mask_p = df_i['_project_key'].isin(sel_proj)
                teams_set = set()
                for t_list in df_i[mask_p]['_involved_teams']:
                    teams_set.update(t_list)
                avail_teams_1 = sorted(list(teams_set))

            sel_team = t1_c3.multiselect("Команда", avail_teams_1, key="f1_team")
            nor_team = t1_c3.checkbox("NOR (исключить)", key="n1_team")

            avail_u1 = gl_users
            if sel_team:
                valid_users = set()
                for login_low, history in user_hist.items():
                    for record in history:
                        if record['team'] in sel_team:
                            if record['start'] <= e_date and record['end'] >= s_date:
                                dname = st.session_state.global_names_cache.get(login_low)
                                if dname: valid_users.add(dname)

                if "Без команды" in sel_team and not df_i.empty:
                    no_team_users = df_i[df_i['_involved_teams'].apply(lambda x: "Без команды" in x)][
                        '_involved_users'].explode()
                    valid_users.update(no_team_users.dropna().unique())

                avail_u1 = sorted(list(valid_users))

            sel_user = t1_c4.multiselect("Сотрудник", avail_u1, key="f1_user")
            nor_user = t1_c4.checkbox("NOR (исключить)", key="n1_user")

            if not df_i.empty:
                def filter_tab1(row):
                    cond_proj = (row['_project_key'] not in sel_proj) if nor_proj else (row['_project_key'] in sel_proj)
                    if not sel_proj: cond_proj = True

                    cond_ind = (row['_industry'] not in sel_ind) if nor_ind else (row['_industry'] in sel_ind)
                    if not sel_ind: cond_ind = True

                    cond_team = set(row['_involved_teams']).isdisjoint(sel_team) if nor_team else not set(row['_involved_teams']).isdisjoint(sel_team)
                    if not sel_team: cond_team = True

                    cond_user = set(row['_involved_users']).isdisjoint(sel_user) if nor_user else not set(row['_involved_users']).isdisjoint(sel_user)
                    if not sel_user: cond_user = True

                    return cond_proj and cond_ind and cond_team and cond_user

                df_i_final = df_i[df_i.apply(filter_tab1, axis=1)].drop(
                    columns=['_project_key', '_involved_teams', '_involved_users', '_industry'])
                st.dataframe(df_i_final.style.map(color_budget, subset=['Коэф.']).format(
                    {"Коэф.": "{:.2f}", "Факт": "{:.2f}", "Медиана": "{:.0f}"}),
                             column_config={"Задача": st.column_config.LinkColumn("Задача",
                                                                                  display_text="https://.*/browse/(.*)")},
                             use_container_width=True, hide_index=True)

                with st.expander("📤 Экспорт в Confluence"):
                    page_id_tasks = st.text_input("Confluence Page ID (Задачи)",
                                                  value=config.get("page_id_tasks", ""),
                                                  key="pid_tasks")

                    if st.button("Экспортировать (Задачи)"):
                        if not page_id_tasks:
                            st.error("Введите Page ID!")
                        else:
                            current_conf = load_config()
                            current_conf['page_id_tasks'] = page_id_tasks
                            save_config(current_conf)

                            c_url = config.get("conf_url")
                            c_user = config.get("conf_user")
                            c_token = config.get("conf_token")
                            c_type = config.get("conf_type", "Cloud")
                            is_cloud = (c_type == "Cloud")

                            if not (c_url and c_token):
                                st.error("Настройки подключения Confluence не заполнены")
                            else:
                                df_export = format_links_in_df(df_i_final, ["Задача"])
                                table_html = df_to_confluence_html(df_export)

                                if 'Вид работ' in df_export.columns:
                                    final_html = wrap_with_chart_macro(
                                        table_html,
                                        chart_type="Pie",
                                        label_col="Вид работ",
                                        value_col="Факт",
                                        title="Распределение по Видам Работ"
                                    )
                                else:
                                    final_html = f"<h3>Список Задач</h3>{table_html}"

                                res, msg = export_to_confluence_advanced(
                                    c_url, c_user, c_token, page_id_tasks, "Отчет: Задачи",
                                    final_html, attachments=[], is_cloud=is_cloud, report_period=period_str
                                )

                                if res:
                                    base_conf_url = c_url.rstrip('/')
                                    page_link = f"{base_conf_url}/pages/viewpage.action?pageId={page_id_tasks}"
                                    st.success(f"✅ Успешно опубликовано! [Открыть страницу {page_id_tasks}]({page_link})")
                                else:
                                    st.error(msg)
            else:
                st.info("Нет данных за выбранный период")

        elif selected_tab == "🚀 Эпики":
            df_e = pd.DataFrame(epics_flat_rows) if epics_flat_rows else pd.DataFrame(
                columns=['_project_key', 'Эпик', '_industry', 'TeamsList', 'Сотрудник'])

            ce1, ce2, ce3, ce4, ce5 = st.columns(5)

            sel_ep_proj = ce1.multiselect("Проект", gl_projects, key="f2_proj")
            nor_ep_proj = ce1.checkbox("NOR", key="n2_proj")

            sel_ep_ind = ce2.multiselect("Отрасль", gl_inds, key="f2_ind")
            nor_ep_ind = ce2.checkbox("NOR", key="n2_ind")

            if not df_e.empty:
                df_e_p = df_e[df_e['_project_key'].isin(sel_ep_proj)] if sel_ep_proj else df_e
                all_epic_urls = sorted(df_e_p['Эпик'].unique())
                url_to_key = {u: u.split('/')[-1] for u in all_epic_urls}
                sel_ep_keys = ce3.multiselect("Эпик", sorted(list(url_to_key.values())), key="f2_epic")
                sel_ep_urls = [k for k, v in url_to_key.items() if v in sel_ep_keys]
                nor_ep_urls = ce3.checkbox("NOR", key="n2_epic")
            else:
                sel_ep_urls = []
                nor_ep_urls = False
                ce3.multiselect("Эпик", [])

            avail_teams_2 = gl_teams
            if sel_ep_proj and not df_e.empty:
                mask_p = df_e['_project_key'].isin(sel_ep_proj)
                teams_set = set()
                for t_list in df_e[mask_p]['TeamsList']:
                    teams_set.update(t_list)
                avail_teams_2 = sorted(list(teams_set))

            sel_et = ce4.multiselect("Команда", avail_teams_2, key="f2_team")
            nor_et = ce4.checkbox("NOR", key="n2_team")

            avail_u2 = gl_users
            if sel_et:
                valid_users = set()
                for login_low, history in user_hist.items():
                    for record in history:
                        if record['team'] in sel_et:
                            if record['start'] <= e_date and record['end'] >= s_date:
                                dname = st.session_state.global_names_cache.get(login_low)
                                if dname: valid_users.add(dname)

                if "Без команды" in sel_et and not df_e.empty:
                    no_team_users = df_e[df_e['TeamsList'].apply(lambda x: not x)]['Сотрудник'].unique()
                    valid_users.update(no_team_users)

                avail_u2 = sorted(list(valid_users))

            sel_eu = ce5.multiselect("Сотрудник", avail_u2, key="f2_user")
            nor_eu = ce5.checkbox("NOR", key="n2_user")

            if not df_e.empty:
                def filter_epics_row(row):
                    cond_proj = (row['_project_key'] not in sel_ep_proj) if nor_ep_proj else (row['_project_key'] in sel_ep_proj)
                    if not sel_ep_proj: cond_proj = True

                    cond_ind = (row['_industry'] not in sel_ep_ind) if nor_ep_ind else (row['_industry'] in sel_ep_ind)
                    if not sel_ep_ind: cond_ind = True

                    cond_ep = (row['Эпик'] not in sel_ep_urls) if nor_ep_urls else (row['Эпик'] in sel_ep_urls)
                    if not sel_ep_urls: cond_ep = True

                    ut = row["TeamsList"]
                    if not ut: in_team = "Без команды" in sel_et
                    else: in_team = not set(ut).isdisjoint(set(sel_et))
                    cond_team = (not in_team) if nor_et else in_team
                    if not sel_et: cond_team = True

                    cond_user = (row['Сотрудник'] not in sel_u) if nor_eu else (row['Сотрудник'] in sel_eu)
                    if not sel_eu: cond_user = True

                    return cond_proj and cond_ind and cond_ep and cond_team and cond_user

                df_ef = df_e[df_e.apply(filter_epics_row, axis=1)]
                st.dataframe(df_ef[["Эпик", "Сотрудник", "Задача", "Тема", "Отрасль", "Списано (ч)"]],
                             column_config={
                                 "Эпик": st.column_config.LinkColumn("Эпик", display_text=r"https://.*/browse/(.*)"),
                                 "Задача": st.column_config.LinkColumn("Задача", display_text=r"https://.*/browse/(.*)"),
                                 "Списано (ч)": st.column_config.NumberColumn(format="%.2f")
                             }, use_container_width=True, hide_index=True)

                ce_sum1, ce_sum2 = st.columns(2)
                df_epic_summary = df_ef.groupby(["Эпик", "Эпик Название"])["Списано (ч)"].sum().reset_index().sort_values(
                    "Списано (ч)", ascending=False)
                df_epic_summary.columns = ["Эпик (Ссылка)", "Название", "Сумма (ч)"]
                ce_sum1.markdown("##### По Эпикам")
                ce_sum1.dataframe(df_epic_summary, column_config={
                    "Эпик (Ссылка)": st.column_config.LinkColumn(display_text=r"https://.*/browse/(.*)"),
                    "Сумма (ч)": st.column_config.NumberColumn(format="%.2f")}, use_container_width=True, hide_index=True)

                df_proj_summary = df_ef.groupby("_project_key")["Списано (ч)"].sum().reset_index().sort_values(
                    "Списано (ч)", ascending=False)
                df_proj_summary.columns = ["Проект", "Сумма (ч)"]
                ce_sum2.markdown("##### По Проектам")
                ce_sum2.dataframe(df_proj_summary,
                                  column_config={"Сумма (ч)": st.column_config.NumberColumn(format="%.2f")},
                                  use_container_width=True, hide_index=True)

                with st.expander("📤 Экспорт в Confluence"):
                    page_id_epics = st.text_input("Confluence Page ID (Эпики)",
                                                  value=config.get("page_id_epics", ""),
                                                  key="pid_epics")

                    if st.button("Экспортировать (Эпики)"):
                        if not page_id_epics:
                            st.error("Введите Page ID!")
                        else:
                            current_conf = load_config()
                            current_conf['page_id_epics'] = page_id_epics
                            save_config(current_conf)

                            c_url = config.get("conf_url")
                            c_user = config.get("conf_user")
                            c_token = config.get("conf_token")
                            c_type = config.get("conf_type", "Cloud")
                            is_cloud = (c_type == "Cloud")

                            if not (c_url and c_token):
                                st.error("Настройки подключения Confluence не заполнены")
                            else:
                                html_body = ""
                                cols_to_link = ["Эпик", "Задача"]
                                df_ef_exp = format_links_in_df(df_ef[["Эпик", "Сотрудник", "Задача", "Тема", "Отрасль", "Списано (ч)"]], cols_to_link)

                                tbl1_html = df_to_confluence_html(df_ef_exp)

                                if 'Отрасль' in df_ef_exp.columns:
                                    html_body += wrap_with_chart_macro(
                                        tbl1_html, "Pie", "Отрасль", "Списано (ч)", "Детализация (по Отраслям)"
                                    )
                                else:
                                    html_body += f"<h3>Детализация по задачам</h3>{tbl1_html}"

                                df_ep_sum_exp = format_links_in_df(df_epic_summary, ["Эпик (Ссылка)"])
                                tbl2_html = df_to_confluence_html(df_ep_sum_exp)

                                html_body += wrap_with_chart_macro(
                                    tbl2_html, "Pie", "Название", "Сумма (ч)", "Топ Эпиков"
                                )

                                tbl3_html = df_to_confluence_html(df_proj_summary)
                                html_body += wrap_with_chart_macro(
                                    tbl3_html, "Pie", "Проект", "Сумма (ч)", "Распределение по Проектам"
                                )

                                res, msg = export_to_confluence_advanced(
                                    c_url, c_user, c_token, page_id_epics, "Отчет: Эпики",
                                    html_body, attachments=[], is_cloud=is_cloud, report_period=period_str
                                )
                                if res:
                                    base_conf_url = c_url.rstrip('/')
                                    page_link = f"{base_conf_url}/pages/viewpage.action?pageId={page_id_epics}"
                                    st.success(f"✅ Успешно опубликовано! [Открыть страницу {page_id_epics}]({page_link})")
                                else:
                                    st.error(msg)
            else:
                st.info("Нет данных по эпикам")

        elif selected_tab == "💼 Портфели":
            st.markdown("### Анализ Портфелей (По связи 'Декомпозирована на')")

            portfolios_data = {}
            epics_map = {}

            # 1. Извлекаем данные из основной выборки задач
            for iss in all_iss:
                f = iss['fields']
                k = iss['key']
                issue_type = f.get('issuetype', {}).get('name', '')

                # Сразу извлекаем авторов для любых типов (и Портфелей, и Эпиков)
                creator = f.get('creator', {}).get('displayName', 'Unknown')
                assignee = f.get('assignee')
                assignee_name = assignee.get('displayName', 'Unassigned') if assignee else 'Unassigned'

                if issue_type == 'Портфель проектов':
                    portfolios_data[k] = {
                        "summary": f.get('summary', ''),
                        "link": f"https://{jira_domain}/browse/{k}",
                        "type": issue_type,
                        "creator": creator,
                        "assignee": assignee_name
                    }
                elif issue_type in ['Epic', 'Эпик']:
                    parent_portfolio = None
                    for link in f.get('issuelinks', []):
                        l_type = link.get('type', {})
                        name_str = (l_type.get('name', '') + l_type.get('inward', '') + l_type.get('outward', '')).lower()
                        if 'декомпозирована' in name_str:
                            target = link.get('outwardIssue') or link.get('inwardIssue')
                            if target: parent_portfolio = target.get('key')

                    epics_map[k] = {
                        "epic_key": k,
                        "epic_summary": f.get('summary', ''),
                        "creator": creator,
                        "assignee": assignee_name,
                        "parent_portfolio": parent_portfolio,
                        "total_hours": 0.0,
                        "type": issue_type
                    }

            # 2. Добавляем данные из кэша (Портфели и Эпики, загруженные рекурсивно)
            for e_k, e_summary in st.session_state.extra_epics_cache.items():
                e_type = st.session_state.extra_types_cache.get(e_k, 'Unknown')

                users_info = st.session_state.extra_users_cache.get(e_k, {})
                c_name = users_info.get("creator", "Unknown (Из связи)")
                a_name = users_info.get("assignee", "Unknown (Из связи)")

                if e_type == 'Портфель проектов':
                    portfolios_data[e_k] = {
                        "summary": e_summary,
                        "link": f"https://{jira_domain}/browse/{e_k}",
                        "type": e_type,
                        "creator": c_name,
                        "assignee": a_name
                    }
                elif e_type in ['Epic', 'Эпик'] and e_k not in epics_map:
                    epics_map[e_k] = {
                        "epic_key": e_k,
                        "epic_summary": e_summary,
                        "creator": c_name,
                        "assignee": a_name,
                        "parent_portfolio": st.session_state.extra_parent_map.get(e_k),
                        "total_hours": 0.0,
                        "type": e_type
                    }

            # Дополнительная проверка на кросс-связи (если портфель ссылается на эпик)
            for k, parent_k in st.session_state.extra_parent_map.items():
                if parent_k and k in epics_map and not epics_map[k]["parent_portfolio"]:
                    epics_map[k]["parent_portfolio"] = parent_k

            # 3. Суммируем часы из ворклогов PRESALE
            for agg_key, wl_data in wl_aggregator.items():
                issue_k = wl_data["issue_key"]
                if issue_k.split('-')[0] == 'PRESALE':
                    task_obj = next((i for i in all_iss if i['key'] == issue_k), None)
                    if task_obj:
                        ep_link = task_obj['fields'].get(epic_link_id)
                        if ep_link and ep_link in epics_map:
                            epics_map[ep_link]["total_hours"] += wl_data["seconds"] / 3600

            # 4. Собираем таблицу
            portfolio_rows = []
            for ep_k, ep_v in epics_map.items():
                if not ep_k.startswith('PROJECT-'):
                    continue

                parent_key = ep_v["parent_portfolio"]
                port_name = "—"
                port_link = "—"
                port_type = "—"
                port_creator = "—"
                port_assignee = "—"

                if parent_key:
                    if parent_key in portfolios_data:
                        port_name = portfolios_data[parent_key]["summary"]
                        port_link = portfolios_data[parent_key]["link"]
                        port_type = portfolios_data[parent_key]["type"]
                        port_creator = portfolios_data[parent_key]["creator"]
                        port_assignee = portfolios_data[parent_key]["assignee"]
                    else:
                        port_name = str(parent_key)

                portfolio_rows.append({
                    "Тип (Портфель)": port_type,
                    "Портфель": port_link,
                    "Название Портфеля": port_name,
                    "Автор Портфеля": port_creator,
                    "Исп. Портфеля": port_assignee,
                    "Тип (Эпик)": ep_v["type"],
                    "Эпик": f"https://{jira_domain}/browse/{ep_k}",
                    "Название Эпика": ep_v["epic_summary"],
                    "Автор Эпика": ep_v["creator"],
                    "Исп. Эпика": ep_v["assignee"],
                    "Часы (PRESALE)": round(ep_v["total_hours"], 2)
                })

            df_port = pd.DataFrame(portfolio_rows)

            if not df_port.empty:
                c_port1, c_port2 = st.columns(2)

                # --- ФИЛЬТР: ПОРТФЕЛИ (Теперь слева - c_port1) ---
                all_port_users = sorted(list(set(df_port["Автор Портфеля"]).union(set(df_port["Исп. Портфеля"]))))
                sel_port_user = c_port1.multiselect("Сотрудник (Автор или Исп. Портфеля)", all_port_users, key="f_port_user")
                nor_port_user = c_port1.checkbox("NOR (Исключить портфели сотрудника)", key="n_port_user")

                # --- ФИЛЬТР: ЭПИКИ (Теперь справа - c_port2) ---
                all_epic_users = sorted(list(set(df_port["Автор Эпика"]).union(set(df_port["Исп. Эпика"]))))
                sel_epic_user = c_port2.multiselect("Сотрудник (Автор или Исп. Эпика)", all_epic_users, key="f_epic_user")
                nor_epic_user = c_port2.checkbox("NOR (Исключить эпики сотрудника)", key="n_epic_user")

                def filter_portfolios(row):
                    # Логика Портфеля
                    if not sel_port_user:
                        cond_port = True
                    else:
                        is_port_involved = (row["Автор Портфеля"] in sel_port_user) or (row["Исп. Портфеля"] in sel_port_user)
                        cond_port = not is_port_involved if nor_port_user else is_port_involved

                    # Логика Эпика
                    if not sel_epic_user:
                        cond_epic = True
                    else:
                        is_epic_involved = (row["Автор Эпика"] in sel_epic_user) or (row["Исп. Эпика"] in sel_epic_user)
                        cond_epic = not is_epic_involved if nor_epic_user else is_epic_involved

                    return cond_epic and cond_port

                df_port_final = df_port[df_port.apply(filter_portfolios, axis=1)]
                df_port_final = df_port_final.sort_values(by=["Название Портфеля", "Автор Эпика"])

                st.dataframe(
                    df_port_final,
                    column_config={
                        "Портфель": st.column_config.LinkColumn("Портфель (Ссылка)", display_text=r"https://.*/browse/(.*)"),
                        "Эпик": st.column_config.LinkColumn("Эпик (Ссылка)", display_text=r"https://.*/browse/(.*)"),
                        "Часы (PRESALE)": st.column_config.NumberColumn(format="%.2f")
                    },
                    use_container_width=True,
                    hide_index=True
                )

                st.markdown("##### Сумма часов по Портфелям")
                df_port_summary = df_port_final.groupby(["Портфель", "Название Портфеля"])["Часы (PRESALE)"].sum().reset_index().sort_values("Часы (PRESALE)", ascending=False)
                st.dataframe(
                    df_port_summary,
                    column_config={"Портфель": st.column_config.LinkColumn(display_text=r"https://.*/browse/(.*)")},
                    use_container_width=True, hide_index=True
                )

                # --- ЭКСПОРТ В CONFLUENCE ---
                with st.expander("📤 Экспорт в Confluence"):
                    page_id_ports = st.text_input("Confluence Page ID (Портфели)",
                                                  value=config.get("page_id_ports", ""),
                                                  key="pid_ports")

                    if st.button("Экспортировать (Портфели)"):
                        if not page_id_ports:
                            st.error("Введите Page ID!")
                        else:
                            current_conf = load_config()
                            current_conf['page_id_ports'] = page_id_ports
                            save_config(current_conf)

                            c_url = config.get("conf_url")
                            c_user = config.get("conf_user")
                            c_token = config.get("conf_token")
                            c_type = config.get("conf_type", "Cloud")
                            is_cloud = (c_type == "Cloud")

                            if not (c_url and c_token):
                                st.error("Настройки подключения Confluence не заполнены")
                            else:
                                html_body = ""
                                # Форматируем ссылки для Confluence
                                cols_to_link = ["Портфель", "Эпик"]
                                df_port_exp = format_links_in_df(df_port_final, cols_to_link)
                                tbl1_html = df_to_confluence_html(df_port_exp)
                                html_body += f"<h3>Анализ Портфелей</h3>{tbl1_html}"

                                df_port_sum_exp = format_links_in_df(df_port_summary, ["Портфель"])
                                tbl2_html = df_to_confluence_html(df_port_sum_exp)

                                # Добавляем круговую диаграмму по портфелям
                                html_body += wrap_with_chart_macro(
                                    tbl2_html, "Pie", "Название Портфеля", "Часы (PRESALE)", "Сумма часов по Портфелям"
                                )

                                res, msg = export_to_confluence_advanced(
                                    c_url, c_user, c_token, page_id_ports, "Отчет: Портфели",
                                    html_body, attachments=[], is_cloud=is_cloud, report_period=period_str
                                )
                                if res:
                                    base_conf_url = c_url.rstrip('/')
                                    page_link = f"{base_conf_url}/pages/viewpage.action?pageId={page_id_ports}"
                                    st.success(f"✅ Успешно опубликовано! [Открыть страницу {page_id_ports}]({page_link})")
                                else:
                                    st.error(msg)
            else:
                st.info("Нет данных. Убедитесь, что задачи PRESALE привязаны к эпикам PROJECT, а те, в свою очередь, связаны связью 'Декомпозирована на'.")

        elif selected_tab == "👥 Детализация":
            df_w = pd.DataFrame(rows_wl) if rows_wl else pd.DataFrame()

            cd1, cd2, cd3, cd4 = st.columns(4)

            sel_w_proj = cd1.multiselect("Проект", gl_projects, key="f3_proj")
            nor_w_proj = cd1.checkbox("NOR", key="n3_proj")

            sel_w_ind = cd2.multiselect("Отрасль", gl_inds, key="f3_ind")
            nor_w_ind = cd2.checkbox("NOR", key="n3_ind")

            avail_teams_3 = gl_teams
            if sel_w_proj and not df_w.empty:
                mask_p = df_w['_project_key'].isin(sel_w_proj)
                teams_set = set()
                for t_list in df_w[mask_p]['TeamsList']:
                    teams_set.update(t_list)
                avail_teams_3 = sorted(list(teams_set))

            sel_t = cd3.multiselect("Команда", avail_teams_3, key="f3_team")
            nor_t = cd3.checkbox("NOR", key="n3_team")

            avail_u3 = gl_users
            if sel_t:
                valid_users = set()
                for login_low, history in user_hist.items():
                    for record in history:
                        if record['team'] in sel_t:
                            if record['start'] <= e_date and record['end'] >= s_date:
                                dname = st.session_state.global_names_cache.get(login_low)
                                if dname: valid_users.add(dname)

                if "Без команды" in sel_t and not df_w.empty:
                    no_team_users = df_w[df_w['TeamsList'].apply(lambda x: not x)]['Сотрудник'].unique()
                    valid_users.update(no_team_users)

                avail_u3 = sorted(list(valid_users))

            sel_u = cd4.multiselect("Сотрудник", avail_u3, key="f3_user")
            nor_u = cd4.checkbox("NOR", key="n3_user")

            if not df_w.empty:
                def filter_rows(row):
                    cond_proj = (row['_project_key'] not in sel_w_proj) if nor_w_proj else (row['_project_key'] in sel_w_proj)
                    if not sel_w_proj: cond_proj = True

                    cond_ind = (row['_industry'] not in sel_ind) if nor_w_ind else (row['_industry'] in sel_ind)
                    if not sel_w_ind: cond_ind = True

                    ut = row["TeamsList"]
                    if not ut: in_team = "Без команды" in sel_t
                    else: in_team = not set(ut).isdisjoint(set(sel_t))
                    cond_team = (not in_team) if nor_t else in_team
                    if not sel_t: cond_team = True

                    cond_user = (row['Сотрудник'] not in sel_u) if nor_u else (row['Сотрудник'] in sel_u)
                    if not sel_u: cond_user = True

                    return cond_proj and cond_ind and cond_team and cond_user

                df_f = df_w[df_w.apply(filter_rows, axis=1)]
                st.dataframe(df_f[["Команды", "Сотрудник", "Задача", "Тема", "Вид работ", "Отрасль", "Списано (ч)"]],
                             column_config={"Задача": st.column_config.LinkColumn(display_text="https://.*/browse/(.*)")},
                             use_container_width=True, hide_index=True)

                c_s1, c_s2 = st.columns(2)
                c_s1.markdown("##### По Командам")
                t_agg = {}
                for _, r in df_f.iterrows():
                    for t in r['TeamsList']: t_agg[t] = t_agg.get(t, 0) + r['Списано (ч)']
                    if not r['TeamsList']: t_agg["Без команды"] = t_agg.get("Без команды", 0) + r['Списано (ч)']

                df_team_agg = pd.DataFrame(list(t_agg.items()), columns=["Команда", "Часы"]).sort_values("Часы",
                                                                                                         ascending=False)
                c_s1.dataframe(df_team_agg, use_container_width=True, hide_index=True)
                c_s2.markdown("##### По Сотрудникам")
                df_employee_agg = df_f.groupby("Сотрудник")["Списано (ч)"].sum().reset_index().sort_values("Списано (ч)", ascending=False)
                c_s2.dataframe(df_employee_agg, use_container_width=True, hide_index=True)

                with st.expander("📤 Экспорт в Confluence"):
                    page_id_details = st.text_input("Confluence Page ID (Детализация)",
                                                  value=config.get("page_id_details", ""),
                                                  key="pid_details")

                    if st.button("Экспортировать (Детализация)"):
                        if not page_id_details:
                            st.error("Введите Page ID!")
                        else:
                            current_conf = load_config()
                            current_conf['page_id_details'] = page_id_details
                            save_config(current_conf)

                            c_url = config.get("conf_url")
                            c_user = config.get("conf_user")
                            c_token = config.get("conf_token")
                            c_type = config.get("conf_type", "Cloud")
                            is_cloud = (c_type == "Cloud")

                            if not (c_url and c_token):
                                st.error("Настройки подключения Confluence не заполнены")
                            else:
                                html_body = ""
                                df_f_exp = format_links_in_df(df_f[["Команды", "Сотрудник", "Задача", "Тема", "Вид работ", "Отрасль", "Списано (ч)"]], ["Задача"])
                                tbl1_html = df_to_confluence_html(df_f_exp)

                                if 'Отрасль' in df_f_exp.columns:
                                    html_body += wrap_with_chart_macro(
                                        tbl1_html, "Pie", "Отрасль", "Списано (ч)", "Детальный отчет"
                                    )
                                else:
                                    html_body += f"<h3>Детальный отчет</h3>{tbl1_html}"

                                tbl2_html = df_to_confluence_html(df_team_agg)
                                html_body += wrap_with_chart_macro(
                                    tbl2_html, "Pie", "Команда", "Часы", "Сводка по Командам"
                                )

                                tbl3_html = df_to_confluence_html(df_employee_agg)
                                html_body += wrap_with_chart_macro(
                                    tbl3_html, "Pie", "Сотрудник", "Списано (ч)", "Сводка по Сотрудникам"
                                )

                                res, msg = export_to_confluence_advanced(
                                    c_url, c_user, c_token, page_id_details, "Отчет: Детализация",
                                    html_body, attachments=[], is_cloud=is_cloud, report_period=period_str
                                )
                                if res:
                                    base_conf_url = c_url.rstrip('/')
                                    page_link = f"{base_conf_url}/pages/viewpage.action?pageId={page_id_details}"
                                    st.success(f"✅ Успешно опубликовано! [Открыть страницу {page_id_details}]({page_link})")
                                else:
                                    st.error(msg)
            else:
                st.info("Нет данных")

        elif selected_tab == "✅ QC Темпо":
            current_s_date = s_date
            current_e_date = e_date
            current_projects = sel_projects

            st.write("---")
            with st.expander("1. Выберите сотрудников для загрузки", expanded=True):
                available_teams_map = {}
                if st.session_state.tempo_teams_cache:
                    available_teams_map = st.session_state.tempo_teams_cache
                elif teams_conf:
                    available_teams_map = teams_conf

                team_names_list = sorted(list(available_teams_map.keys()))

                name_to_login_map = {}
                for t_name, members in available_teams_map.items():
                    for m in members:
                        if isinstance(m, dict):
                            lg = m.get('login')
                            nm = m.get('name', lg)
                            if nm and lg:
                                name_to_login_map[nm] = lg

                for lg, nm in st.session_state.global_names_cache.items():
                    if nm not in name_to_login_map:
                        name_to_login_map[nm] = lg

                all_known_names = sorted([str(k) for k in name_to_login_map.keys() if k])

                c_teams, c_users = st.columns(2)

                with c_teams:
                    sel_load_teams = st.multiselect("Команды", team_names_list, placeholder="Выберите команды")

                with c_users:
                    filtered_names = all_known_names
                    if sel_load_teams:
                        subset_logins = set()
                        for t in sel_load_teams:
                            for t_name, members in available_teams_map.items():
                                if t_name == t:
                                    for m in members:
                                        try:
                                            raw_from = m.get('dateFrom')
                                            if raw_from and str(raw_from) not in ['None', '']:
                                                m_from = datetime.strptime(str(raw_from)[:10], '%Y-%m-%d').date()
                                            else:
                                                m_from = date(2000, 1, 1)

                                            raw_to = m.get('dateTo')
                                            if raw_to and str(raw_to) not in ['None', '']:
                                                m_to = datetime.strptime(str(raw_to)[:10], '%Y-%m-%d').date()
                                            else:
                                                m_to = date(2099, 12, 31)

                                            if m_from <= current_e_date and m_to >= current_s_date:
                                                l_val = m.get('login')
                                                if l_val: subset_logins.add(l_val)

                                        except Exception:
                                            pass

                        filtered_names = [n for n in all_known_names if name_to_login_map.get(n) in subset_logins]

                    sel_workers_names = st.multiselect(
                        "Сотрудники",
                        filtered_names,
                        placeholder="Выберите сотрудников..."
                    )

                manual_workers_text = st.text_area(
                    "Добавить логины вручную (через запятую)",
                    help="Например: ivanov, petrov, sidorov",
                    height=68
                )

                target_workers = set()
                selected_teams_map = {}

                if sel_workers_names:
                    login_to_key_map = {}
                    for t_name, members in available_teams_map.items():
                        for m in members:
                            l = m.get('login')
                            k = m.get('key')
                            if l and k:
                                login_to_key_map[l] = k

                    for name in sel_workers_names:
                        login = name_to_login_map.get(name)
                        if login:
                            final_id = login_to_key_map.get(login, login)
                            target_workers.add(final_id)

                            for t_name, members in available_teams_map.items():
                                for m in members:
                                    if m.get('login') == login:
                                        if login not in selected_teams_map: selected_teams_map[login] = set()
                                        selected_teams_map[login].add(t_name)

                elif sel_load_teams:
                    for team_name in sel_load_teams:
                        team_members = available_teams_map.get(team_name, [])
                        for m in team_members:
                            if isinstance(m, dict):
                                try:
                                    d_from = datetime.strptime(m.get('dateFrom', '2000-01-01'), '%Y-%m-%d').date()
                                    d_to = datetime.strptime(m.get('dateTo', '2099-12-31'), '%Y-%m-%d').date()
                                except:
                                    d_from = date(2000, 1, 1)
                                    d_to = date(2099, 12, 31)

                                if not (d_from <= current_e_date and d_to >= current_s_date):
                                    continue

                                lg = m.get('login')
                                ky = m.get('key')
                                target_id = ky if ky else lg
                                if target_id:
                                    target_workers.add(target_id)
                                    user_key = lg if lg else ky
                                    if user_key:
                                        if user_key not in selected_teams_map: selected_teams_map[user_key] = set()
                                        selected_teams_map[user_key].add(team_name)

                if manual_workers_text:
                    parts = [p.strip() for p in manual_workers_text.replace('\n', ',').split(',') if p.strip()]
                    target_workers.update(parts)

                target_workers_list = sorted(list(target_workers))

                st.caption(f"Период загрузки (из Фильтра): **{current_s_date} — {current_e_date}**")
                if target_workers_list:
                    st.caption(f"Будет загружено сотрудников: **{len(target_workers_list)}**")

                st.write("")

                c_btn, c_info = st.columns([1, 3])
                with c_btn:
                    btn_disabled = len(target_workers_list) == 0

                    if st.button("📥 Загрузить данные из Tempo API", type="primary", disabled=btn_disabled):
                        if not api_token:
                            st.error("Нет токена")
                        else:
                            with st.spinner("Загрузка данных из Tempo..."):
                                unknowns = []
                                for w in target_workers_list:
                                    if not str(w).upper().startswith("JIRAUSER"):
                                        unknowns.append(w)

                                if unknowns:
                                    st.info(
                                        f"🔎 Обнаружено {len(unknowns)} логинов без ключей (JIRAUSER). Пытаемся найти ключи...")
                                    resolved_map = {}
                                    prog_bar = st.progress(0)

                                    for idx, u_login in enumerate(unknowns):
                                        u_data = get_jira_user_details(jira_domain, u_login, auth_method, api_token)
                                        real_key = None
                                        if u_data:
                                            real_key = u_data.get('key')

                                        if not real_key:
                                            found_list = search_jira_users(jira_domain, u_login, auth_method, api_token)
                                            if found_list and len(found_list) > 0:
                                                real_key = found_list[0].get('key')

                                        if real_key:
                                            resolved_map[u_login] = real_key

                                        prog_bar.progress((idx + 1) / len(unknowns))

                                    prog_bar.empty()

                                    final_list = []
                                    for w in target_workers_list:
                                        if w in resolved_map:
                                            final_list.append(resolved_map[w])
                                        else:
                                            final_list.append(w)
                                    target_workers_list = final_list
                                    if resolved_map:
                                        st.success(f"✅ Ключи найдены для {len(resolved_map)} сотрудников.")
                                    else:
                                        st.warning(
                                            "⚠️ Не удалось найти ключи JIRAUSER для выбранных логинов. Загрузка может быть неполной.")

                                st.session_state.tempo_accounts_map = fetch_tempo_accounts(jira_domain, auth_method,
                                                                                           api_token)
                                status_ph = st.empty()

                                t_data = fetch_tempo_raw_data(
                                    jira_domain,
                                    current_s_date,
                                    current_e_date,
                                    auth_method,
                                    api_token,
                                    status_ph,
                                    project_keys=current_projects if current_projects else None,
                                    worker_ids=target_workers_list
                                )

                                if t_data:
                                    st.session_state.tempo_raw_worklogs = t_data
                                    user_ids = set(w.get('worker', '').strip() for w in t_data)
                                    resolve_users_bulk(jira_domain, auth_method, api_token, list(user_ids))
                                    save_tempo_cache(t_data, st.session_state.tempo_accounts_map)

                                    with open(TEMPO_DEBUG_FILE, "w", encoding='utf-8') as f:
                                        json.dump(t_data, f, ensure_ascii=False, indent=2)

                                    st.success(f"Загружено {len(t_data)} записей.")
                                else:
                                    st.error("Данных в Tempo не найдено для указанных фильтров.")

                if btn_disabled:
                    st.info("👈 Пожалуйста, выберите хотя бы одного сотрудника или введите логин вручную.")

            if st.session_state.tempo_raw_worklogs:
                st.markdown("---")
                json_str = json.dumps(st.session_state.tempo_raw_worklogs, indent=2, ensure_ascii=False)
                st.download_button(
                    label="💾 Скачать tempo.json (raw)",
                    data=json_str,
                    file_name="tempo.json",
                    mime="application/json"
                )

            if st.session_state.tempo_raw_worklogs:
                user_ids = set(w.get('worker', '').strip() for w in st.session_state.tempo_raw_worklogs)
                resolve_users_bulk(jira_domain, auth_method, api_token, list(user_ids))

                tempo_rows = []
                acc_map = st.session_state.tempo_accounts_map

                for w in st.session_state.tempo_raw_worklogs:
                    qc_flags = []

                    w_date_str = w.get('startDate') or w.get('dateStarted') or w.get('started') or w.get(
                        'dateCreated') or ''
                    w_date_str = str(w_date_str)[:10]

                    if not w_date_str or len(w_date_str) < 10:
                        qc_flags.append("⚠️ Нет даты")
                        w_date_str = current_s_date.strftime('%Y-%m-%d')

                    comment = w.get('comment', '')
                    secs = w.get('timeSpentSeconds', 0)
                    w_issue = w.get('issue', {}).get('key', 'UNKNOWN')
                    worker_id = w.get('worker', '').strip()
                    worklog_id = w.get('tempoWorklogId') or w.get('id') or (w_issue + worker_id + w_date_str + str(secs))

                    acc_key = "—"
                    acc_name = "—"

                    attrs = w.get('attributes', {})
                    if isinstance(attrs, dict):
                        for k, v in attrs.items():
                            if isinstance(v, dict):
                                a_type = v.get('type', '')
                                a_name = v.get('name', '')
                                if a_type == 'ACCOUNT' or 'аккаунт' in a_name.lower() or 'account' in a_name.lower():
                                    val = v.get('value')
                                    if val:
                                        acc_key = str(val)
                                        acc_name = acc_map.get(acc_key, acc_key)
                                        break
                            else:
                                if 'account' in k.lower():
                                    acc_key = str(v)
                                    acc_name = acc_map.get(acc_key, acc_key)
                                    break

                    if acc_key == "—":
                        qc_flags.append("⚠️ Нет аккаунта")

                    is_presale = w_issue.upper().startswith("PRESALE")

                    if is_presale and len(comment) < 170:
                        qc_flags.append("⚠️ Короткий коммент (Presale)")
                    elif len(comment) < 5:
                        qc_flags.append("⚠️ Короткий коммент")

                    u_name = st.session_state.global_names_cache.get(worker_id, worker_id)
                    login = st.session_state.key_to_login_map.get(worker_id)
                    if login and u_name == worker_id:
                        u_name = st.session_state.global_names_cache.get(login, worker_id)

                    curr_teams = set()

                    def check_hist(u_login):
                        if u_login in user_hist:
                            try:
                                wd = datetime.strptime(w_date_str, '%Y-%m-%d').date()
                                for rec in user_hist[u_login]:
                                    if rec['start'] <= wd <= rec['end']: curr_teams.add(rec['team'])
                            except:
                                pass

                    check_hist(worker_id.lower())
                    if login: check_hist(login.lower())

                    if not curr_teams and login and login in selected_teams_map:
                        curr_teams.update(selected_teams_map[login])

                    t_str = ", ".join(sorted(list(curr_teams))) if curr_teams else "Без команды"

                    try:
                        w_date_obj = datetime.strptime(w_date_str, '%Y-%m-%d')
                        t_link_start = (w_date_obj.replace(day=1)).strftime('%Y-%m-%d')
                        t_link_end = (w_date_obj + timedelta(days=32)).replace(day=1) - timedelta(days=1)
                        t_link_end = t_link_end.strftime('%Y-%m-%d')
                        tempo_url = f"https://{jira_domain}/secure/Tempo.jspa#/my-work/timesheet?from={t_link_start}&to={t_link_end}&periodType=FIXED&worker={worker_id}&viewType=TIMESHEET"
                    except:
                        tempo_url = "#"

                    tempo_rows.append({
                        "_id": worklog_id,
                        "_issue_key": w_issue,
                        "Сотрудник": u_name, "Таймшит": tempo_url, "Команда": t_str, "TeamsList": list(curr_teams),
                        "Задача": f"https://{jira_domain}/browse/{w_issue}", "Списано (ч)": round(secs / 3600, 2),
                        "Аккаунт (Ключ)": acc_key, "Аккаунт (Имя)": acc_name, "Коммент": comment,
                        "QC Статус": ", ".join(qc_flags) if qc_flags else "OK"
                    })

                if tempo_rows:
                    df_qc = pd.DataFrame(tempo_rows)
                    df_qc.drop_duplicates(subset=['_id'], inplace=True)

                    qc_c1, qc_c2 = st.columns(2)

                    all_q_t = set()
                    for lst in df_qc['TeamsList']:
                        if lst:
                            for t in lst: all_q_t.add(t)
                        else:
                            all_q_t.add("Без команды")

                    sel_qt = qc_c1.multiselect("Фильтр Команд (Таблица)", sorted(list(all_q_t)), key="qc_t")
                    nor_qt = qc_c1.checkbox("NOR", key="n_qc_t")

                    avail_qu = sorted(df_qc['Сотрудник'].unique())
                    if sel_qt:
                        mask_teams_only = df_qc['TeamsList'].apply(
                            lambda x: not set(x).isdisjoint(sel_qt) if x else "Без команды" in sel_qt)
                        avail_qu = sorted(df_qc[mask_teams_only]['Сотрудник'].unique())

                    sel_qu = qc_c2.multiselect("Фильтр Сотрудников (Таблица)", avail_qu, key="qc_u")
                    nor_qu = qc_c2.checkbox("NOR", key="n_qc_u")

                    mask_qc = pd.Series(True, index=df_qc.index)
                    if sel_qt:
                        in_team_mask = df_qc['TeamsList'].apply(lambda x: not set(x).isdisjoint(sel_qt) if x else "Без команды" in sel_qt)
                        mask_qc &= ~in_team_mask if nor_qt else in_team_mask
                    if sel_qu:
                        in_user_mask = df_qc['Сотрудник'].isin(sel_qu)
                        mask_qc &= ~in_user_mask if nor_qu else in_user_mask

                    df_qc_filtered = df_qc[mask_qc]

                    def highlight_errors(row):
                        if "⚠️" in row['QC Статус']:
                            return ['background-color: #ffcccc; color: black'] * len(row)
                        return [''] * len(row)

                    st.dataframe(
                        df_qc_filtered[["Сотрудник", "Таймшит", "Задача", "Списано (ч)", "Аккаунт (Ключ)", "Аккаунт (Имя)",
                                        "QC Статус", "Коммент"]].style.apply(highlight_errors, axis=1),
                        column_config={
                            "Таймшит": st.column_config.LinkColumn(display_text="Open Tempo"),
                            "Задача": st.column_config.LinkColumn(display_text=r"https://.*/browse/(.*)"),
                            "Списано (ч)": st.column_config.NumberColumn(format="%.2f")
                        }, use_container_width=True, hide_index=True
                    )

                    st.markdown("##### Сводная таблица")
                    df_summary = df_qc_filtered.groupby("Сотрудник")["Списано (ч)"].sum().reset_index().sort_values(
                        "Списано (ч)", ascending=False)
                    st.dataframe(df_summary, use_container_width=True, hide_index=True)

                    st.markdown("### 📄 Генерация текстового отчета")
                    if st.button("Сгенерировать отчет (.txt)"):
                        issue_map = {i['key']: i for i in st.session_state.raw_issues_data}
                        report_text = ""
                        for i_key, grp in df_qc_filtered.groupby("_issue_key"):
                            company = "Unknown Company"
                            summary = ""

                            if i_key in wl_aggregator and "company_name" in wl_aggregator[(i_key, list(wl_aggregator.keys())[0][1])]:
                                 pass

                            if i_key in issue_map:
                                val = issue_map[i_key]['fields'].get(COMPANY_FIELD_ID)

                                if isinstance(val, dict):
                                    company = safe_extract_name(val)
                                elif isinstance(val, list) and val:
                                    raw_str = str(val[0])
                                    match = re.search(r'\((CLIBD-\d+)\)', raw_str)
                                    if match:
                                        company = resolve_insight_object(jira_domain, auth_method, api_token, match.group(1))
                                    else:
                                        company = safe_extract_name(val)
                                else:
                                    company = safe_extract_name(val)

                                summary = issue_map[i_key]['fields'].get('summary', '')

                            products = set()
                            for acc in grp['Аккаунт (Имя)']:
                                if acc and str(acc) != "—":
                                    prod_name = str(acc).split(':')[0].strip()
                                    products.add(prod_name)
                            product_str = ", ".join(sorted(list(products))) if products else "No Product"

                            comments = [c.strip() for c in grp['Коммент'] if c and str(c).strip()]
                            comment_str = ". ".join(comments)

                            if comment_str:
                                report_text += f"{company} ({product_str}) {summary} {comment_str}\n\n"

                        st.download_button("Скачать отчет", report_text, file_name="report_confluence.txt")

                else:
                    st.info("Нет данных (буфер пуст).")

                st.markdown("---")
                with st.expander("🕵️ Debug: Raw JSON (First Record)"):
                    if st.session_state.tempo_raw_worklogs:
                        st.json(st.session_state.tempo_raw_worklogs[0])
                    else:
                        st.write("No data loaded.")

            else:
                st.info("Нажмите кнопку выше, чтобы загрузить данные.")

        elif selected_tab == "📂 Табель":
            st.write("---")

            uploaded_file = st.file_uploader("Загрузите файл T-13 (XLSX)", type=['xlsx'])

            if uploaded_file:
                try:
                    df_raw = pd.read_excel(uploaded_file, header=None)

                    p_start, p_end = extract_period_from_excel(df_raw.head(20))
                    if p_start and p_end:
                        st.success(
                            f"📅 Отчетный период: **{p_start.strftime('%d.%m.%Y')} — {p_end.strftime('%d.%m.%Y')}**")
                    else:
                        st.warning("⚠️ Не удалось автоматически определить период в шапке файла.")

                    header_row_idx = None
                    name_col_idx = None

                    for idx, row in df_raw.iterrows():
                        for col_idx, val in enumerate(row):
                            if str(val).lower().startswith("фамилия"):
                                header_row_idx = idx
                                name_col_idx = col_idx
                                break
                        if header_row_idx is not None: break

                    if header_row_idx is not None:
                        hours_col_idx = None
                        for col_idx in range(len(df_raw.columns) - 1, -1, -1):
                            is_target = False
                            for r in range(header_row_idx - 5, header_row_idx + 5):
                                if r < 0 or r >= len(df_raw): continue
                                val = str(df_raw.iloc[r, col_idx]).lower()
                                if "месяц" in val or "month" in val:
                                    is_target = True
                                    break
                            if is_target:
                                hours_col_idx = col_idx
                                break

                        if hours_col_idx is None:
                            for col_idx in range(len(df_raw.columns) - 1, -1, -1):
                                sample_data = df_raw.iloc[len(df_raw) // 2, col_idx]
                                if pd.notna(sample_data):
                                    hours_col_idx = col_idx
                                    break

                        extracted_rows = []
                        start_data_idx = header_row_idx + 1

                        for i in range(start_data_idx, len(df_raw)):
                            row = df_raw.iloc[i]
                            raw_name = row[name_col_idx]

                            if pd.isna(raw_name): continue
                            s_name = str(raw_name).strip()
                            if len(s_name) < 2: continue

                            s_name_lower = s_name.lower()
                            bad_keywords = ["фамилия", "инициалы", "должность", "итого", "ответственный", "руководитель",
                                            "специальность", "профессия"]
                            if any(k in s_name_lower for k in bad_keywords):
                                continue

                            clean_name = s_name.split('\n')[0].strip()
                            clean_name = clean_name.split('(')[0].strip()

                            candidates = []
                            scan_range = 4
                            for offset in range(scan_range):
                                if i + offset >= len(df_raw): break

                                if offset > 0:
                                    check_next = df_raw.iloc[i + offset, name_col_idx]
                                    if pd.notna(check_next) and len(str(check_next)) > 3:
                                        break

                                val = df_raw.iloc[i + offset, hours_col_idx]
                                try:
                                    val_str = str(val).replace(',', '.').replace(' ', '')
                                    v_float = float(val_str)
                                    candidates.append(v_float)
                                except:
                                    pass

                            if candidates:
                                hours = max(candidates)
                                extracted_rows.append({"Excel Name": clean_name, "Hours": hours})

                        if extracted_rows:
                            df_excel = pd.DataFrame(extracted_rows)

                            all_jira_names = sorted(list(set(st.session_state.global_names_cache.values())))

                            excel_res = []
                            for idx, row in df_excel.iterrows():
                                e_name = row['Excel Name']
                                h_val = row['Hours']

                                found_jira_name = None
                                found_teams = set()

                                for j_name in all_jira_names:
                                    if check_name_match(j_name, e_name):
                                        found_jira_name = j_name
                                        break

                                if found_jira_name:
                                    login = None
                                    for k, v in st.session_state.global_names_cache.items():
                                        if v == found_jira_name:
                                            login = k
                                            break

                                    if login and login in user_hist:
                                        for rec in user_hist[login]:
                                            if p_start and p_end:
                                                if rec['start'] <= p_end and rec['end'] >= p_start:
                                                    found_teams.add(rec['team'])
                                            else:
                                                found_teams.add(rec['team'])

                                excel_res.append({
                                    "Сотрудник (Excel)": e_name,
                                    "Сотрудник (Jira)": found_jira_name if found_jira_name else "—",
                                    "Часы (Табель)": h_val,
                                    "TeamsList": list(found_teams) if found_teams else [],
                                    "Команды": ", ".join(sorted(list(found_teams))) if found_teams else "Без команды"
                                })

                            df_res = pd.DataFrame(excel_res)

                            st.subheader("📋 Сверка часов")

                            all_found_teams = set()
                            for t_list in df_res['TeamsList']:
                                all_found_teams.update(t_list)
                            if not all_found_teams: all_found_teams.add("Без команды")

                            col_t_filter, col_empty = st.columns([1, 2])
                            sel_teams_filter = col_t_filter.multiselect("Фильтр по команде", sorted(list(all_found_teams)))

                            if sel_teams_filter:
                                mask = df_res['TeamsList'].apply(lambda x: not set(x).isdisjoint(sel_teams_filter) if x else "Без команды" in sel_teams_filter)
                                df_final = df_res[mask]
                            else:
                                df_final = df_res

                            norm_hours = df_res['Часы (Табель)'].max()
                            if pd.isna(norm_hours): norm_hours = 0

                            st.info(f"ℹ️ Норма часов (максимальное значение в файле): **{int(norm_hours)}**")

                            def style_underworked(row):
                                h = row['Часы (Табель)']
                                if h < norm_hours:
                                    return ['background-color: #ffe6e6; color: black'] * len(
                                        row)
                                return [''] * len(row)

                            st.dataframe(
                                df_final[["Сотрудник (Excel)", "Сотрудник (Jira)", "Команды", "Часы (Табель)"]].style.apply(
                                    style_underworked, axis=1).format({"Часы (Табель)": "{:.0f}"}),
                                use_container_width=True,
                                hide_index=True
                            )

                        else:
                            st.warning(
                                "Не удалось извлечь данные о часах. Возможно, формат файла отличается от ожидаемого T-13.")
                    else:
                        st.error("Не удалось найти строку заголовка (с ячейкой 'Фамилия').")

                except Exception as e:
                    st.error(f"Ошибка при чтении файла: {e}")

# --- FOOTER ---
st.markdown("---")
st.markdown("""<div style='text-align: center; color: grey; font-size: 14px;'>Разработано в <b>Integration Team</b> 2026</div>""", unsafe_allow_html=True)
