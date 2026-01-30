import streamlit as st
import requests
import pandas as pd
import json
import os
from datetime import datetime, timedelta

# --- КОНФИГУРАЦИЯ ---
st.set_page_config(page_title="Jira Budget Monitor", layout="wide")
CONFIG_FILE = "config.json"

# --- ФУНКЦИИ ---
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
        st.sidebar.success("✅ Настройки сохранены!")
    except Exception as e:
        st.sidebar.error(f"Ошибка сохранения: {e}")

# Функция раскраски ячеек (КОЭФФИЦИЕНТЫ)
def color_budget(val):
    if val < 0.49:
        return 'background-color: #66ff66; color: black' # Зеленый
    elif 0.49 <= val <= 1.00:
        return 'background-color: #d9ffcc; color: black' # Светло-зеленый
    elif 1.00 < val < 2.00:
        return 'background-color: #ffcccc; color: black' # Розовый
    elif 2.00 <= val < 5.00:
        return 'background-color: #ff4d4d; color: black' # Красный
    else:
        return 'background-color: #800000; color: white'   # Бордовый

config = load_config()

# --- ЗАГОЛОВОК ---
st.header("📊 Jira Budget Monitor")

# Инициализация Session State
if 'calculated_medians' not in st.session_state:
    st.session_state.calculated_medians = {}
if 'df_results' not in st.session_state:
    st.session_state.df_results = None

# --- БОКОВАЯ ПАНЕЛЬ ---
with st.sidebar:
    st.header("1. Настройки")

    jira_domain = st.text_input("Jira Domain", value=config.get("domain", "jira.company.com"))
    auth_idx = config.get("auth_index", 0)
    auth_method = st.radio("Метод авторизации", ["Personal Access Token (PAT)", "Browser Cookie (JSESSIONID)"], index=auth_idx)
    api_token = st.text_input("Токен / Cookie", value=config.get("token", ""), type="password")

    st.markdown("---")
    st.header("2. Проект")
    project_key = st.text_input("Ключ проекта", value=config.get("project", "PRESALE"))
    work_type_field_id = st.text_input("ID поля 'Вид работ'", value=config.get("field_id", "customfield_22202"))

    if st.button("💾 Сохранить настройки"):
        new_config = {
            "domain": jira_domain,
            "auth_index": 0 if auth_method == "Personal Access Token (PAT)" else 1,
            "token": api_token,
            "project": project_key,
            "field_id": work_type_field_id
        }
        save_config(new_config)

    st.markdown("---")
    st.header("3. Нормативы (Медианы)")

    median_depth = st.selectbox("Глубина истории", ["1 год", "2 года", "3 года", "Все время"])
    calc_medians_btn = st.button("🔄 Пересчитать медианы")

# --- API ---
def make_jira_request(url, params, method, token):
    requests.packages.urllib3.disable_warnings()
    headers = {"Content-Type": "application/json"}
    cookies = {}

    if method == "Personal Access Token (PAT)":
        headers["Authorization"] = f"Bearer {token}"
    else:
        cookies = {"JSESSIONID": token}

    try:
        response = requests.get(url, headers=headers, params=params, cookies=cookies, verify=False)
        if response.status_code in [400, 401]:
            return None
        response.raise_for_status()
        return response.json()
    except Exception:
        return None

def get_all_issues(domain, jql, fields_list, method, token):
    all_issues = []
    start_at = 0
    max_results = 100
    api_url = f"https://{domain}/rest/api/2/search"
    fields_string = ",".join(fields_list)

    progress_bar = st.progress(0)

    while True:
        params = {"jql": jql, "fields": fields_string, "startAt": start_at, "maxResults": max_results}
        data = make_jira_request(api_url, params, method, token)
        if not data or 'issues' not in data:
            break
        issues = data['issues']
        all_issues.extend(issues)
        total = data.get('total', 0)
        if total > 0:
            progress_bar.progress(min(len(all_issues) / total, 1.0))
        if start_at + len(issues) >= total:
            break
        start_at += len(issues)

    progress_bar.empty()
    return all_issues

# --- ЛОГИКА МЕДИАН ---
if calc_medians_btn:
    if not api_token:
        st.error("Нужен токен!")
    else:
        days_map = {"1 год": 365, "2 года": 730, "3 года": 1095, "Все время": 0}
        days = days_map[median_depth]
        date_jql = f"AND created >= '{(datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')}'" if days > 0 else ""
        jql = f"project = {project_key} AND timespent > 0 {date_jql}"

        with st.spinner("Считаем статистику..."):
            issues = get_all_issues(jira_domain, jql, ["timespent", work_type_field_id], auth_method, api_token)

        if issues:
            data = []
            for i in issues:
                raw = i['fields'].get(work_type_field_id)
                w_type = "Не указан"
                if isinstance(raw, dict): w_type = raw.get('value', 'Unknown')
                elif isinstance(raw, list) and raw: w_type = raw[0].get('value', 'Unknown')
                elif raw: w_type = str(raw)

                data.append({"Type": w_type, "Hours": (i['fields'].get('timespent') or 0)/3600})

            if data:
                df_med = pd.DataFrame(data)
                st.session_state.calculated_medians = df_med.groupby("Type")["Hours"].median().to_dict()
                st.success(f"Готово! Задач: {len(issues)}")
            else:
                st.warning("Нет списаний времени.")

with st.sidebar:
    if st.session_state.calculated_medians:
        st.write("📊 **Нормативы:**")
        st.dataframe(pd.DataFrame(list(st.session_state.calculated_medians.items()), columns=["Вид", "Часы"]), hide_index=True, use_container_width=True)

# --- АНАЛИЗ ---
st.markdown("### 4. Анализ задач")
c1, c2 = st.columns([3, 1])
with c1:
    d_range = st.date_input("Период создания", (datetime.now()-timedelta(days=30), datetime.now()), format="DD.MM.YYYY")
with c2:
    st.write("")
    st.write("")
    if st.button("🚀 Запустить", type="primary", use_container_width=True):
        if not st.session_state.calculated_medians:
            st.warning("Сначала рассчитайте медианы!")
        else:
            s_date, e_date = d_range
            jql = f"project = {project_key} AND timespent > 0 AND created >= '{s_date.strftime('%Y-%m-%d')}' AND created <= '{e_date.strftime('%Y-%m-%d')}'"
            fields = ["key", "summary", "timespent", "status", "creator", "labels", work_type_field_id]

            with st.spinner("Загрузка..."):
                res = get_all_issues(jira_domain, jql, fields, auth_method, api_token)

            if res:
                rows = []
                medians = st.session_state.calculated_medians
                for i in res:
                    f = i['fields']
                    raw = f.get(work_type_field_id)
                    w_type = "Не указан"
                    if isinstance(raw, dict): w_type = raw.get('value', 'Unknown')
                    elif isinstance(raw, list) and raw: w_type = raw[0].get('value', 'Unknown')
                    elif raw: w_type = str(raw)

                    labels = f.get('labels', [])
                    direction = next((l for l in labels if l.startswith("ДВ_")), "")

                    spent = round((f.get('timespent', 0) or 0)/3600, 2)
                    med = medians.get(w_type, 0)

                    # Коэффициент
                    coef = (spent / med) if med > 0 else 0

                    link = f"https://{jira_domain}/browse/{i['key']}"

                    rows.append({
                        "Задача": link,
                        "Тема": f['summary'],
                        "Статус": f.get('status', {}).get('name'),
                        "Автор": f.get('creator', {}).get('displayName'),
                        "Направление": direction,
                        "Вид": w_type,
                        "Факт": spent,
                        "Медиана": med,
                        "Коэф.": coef
                    })
                st.session_state.df_results = pd.DataFrame(rows)
            else:
                st.info("Задач не найдено.")

# --- ВЫВОД РЕЗУЛЬТАТОВ ---
if st.session_state.df_results is not None:
    df = st.session_state.df_results.copy()

    with st.expander("🔍 Фильтрация результатов", expanded=True):
        fc1, fc2, fc3, fc4 = st.columns(4)
        with fc1:
            sel_auth = st.multiselect("Автор", sorted(df["Автор"].unique()))
        with fc2:
            sel_dir = st.multiselect("Направление", sorted(df["Направление"].unique()))
        with fc3:
            sel_type = st.multiselect("Вид работ", sorted(df["Вид"].unique()))
        with fc4:
            sel_status = st.multiselect("Статус", sorted(df["Статус"].unique()))

    if sel_auth: df = df[df["Автор"].isin(sel_auth)]
    if sel_dir: df = df[df["Направление"].isin(sel_dir)]
    if sel_type: df = df[df["Вид"].isin(sel_type)]
    if sel_status: df = df[df["Статус"].isin(sel_status)]

    st.markdown(f"### Результаты: {len(df)} задач")

    # Раскраска (Styler)
    styled_df = df.style.map(color_budget, subset=['Коэф.'])
    styled_df = styled_df.format({"Коэф.": "{:.2f}", "Факт": "{:.2f}", "Медиана": "{:.0f}"})

    st.dataframe(
        styled_df,
        column_config={
            "Задача": st.column_config.LinkColumn(
                "Задача",
                display_text="https://.*/browse/(.*)",
                help="Открыть в Jira"
            )
        },
        hide_index=True,
        use_container_width=True
    )

# --- FOOTER (ПОДВАЛ) ---
st.markdown("---")
st.markdown(
    """
    <div style='text-align: center; color: grey; font-size: 14px;'>
        Разработано в <b>Integration Team</b> 2026
    </div>
    """,
    unsafe_allow_html=True
)
