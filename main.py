# main.py
import io
import threading
from collections import deque
from datetime import date

import matplotlib
import matplotlib.patheffects as path_effects
import matplotlib.pyplot as plt
import pymysql
import requests
import seaborn as sns
from flask import Flask, request

# Import all configuration variables from config.py
from config import (ACCESS_TOKEN, DB_ATTENDANCE_CONFIG, DB_HIERARCHY_CONFIG,
                    PHONE_NUMBER_ID, VERIFY_TOKEN)

# --- APP INITIALIZATION ---
matplotlib.use('Agg')
app = Flask(__name__)

# --- DEDUPLICATION MECHANISM ---
PROCESSED_MESSAGE_IDS = deque(maxlen=1000)


# --- BACKGROUND WORKER FUNCTION ---
def process_message_in_background(payload):
    with app.app_context():
        try:
            value = payload.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {})
            message_data = value.get("messages", [{}])[0]
            sender_phone = message_data.get("from")

            if not sender_phone or not message_data:
                return

            user_details = get_user_details(sender_phone)
            if not user_details:
                send_text_message(sender_phone, "❌ Your phone number is not registered in the system.")
                return

            user_role = user_details.get('role')
            if not user_role:
                send_text_message(sender_phone, "❌ No role found for your account.")
                return

            if user_role == 'Supervisor':
                handle_supervisor_flow(sender_phone, user_details, message_data)
            elif user_role == 'PM':
                handle_pm_flow(sender_phone, user_details, message_data)
            elif user_role == 'Executive':
                handle_executive_flow(sender_phone, user_details, message_data)
            else:
                send_text_message(sender_phone, f"Your role ({user_role}) does not have a defined report flow.")
        except Exception as e:
            print(f"Error in background thread: {e}")
            import traceback
            traceback.print_exc()


# --- WEBHOOK HANDLER ---
@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == VERIFY_TOKEN:
            return challenge, 200
        else:
            return "Verification failed", 403
    elif request.method == 'POST':
        try:
            data = request.get_json()
            if not data or 'entry' not in data:
                return "OK", 200
            value = data.get('entry', [{}])[0].get('changes', [{}])[0].get('value', {})
            if 'statuses' in value:
                return "OK", 200
            messages = value.get('messages', [])
            if not messages:
                return "OK", 200
            message = messages[0]
            message_id = message.get('id')
            if not message_id:
                return "OK", 200
            sender_phone = message.get('from')
            if not sender_phone or sender_phone == PHONE_NUMBER_ID:
                return "OK", 200

            message_type = message.get('type', 'unknown')
            if message_type == 'text':
                content = message.get('text', {}).get('body', '')[:50]
            elif message_type == 'interactive':
                interactive_data = message.get('interactive', {})
                if interactive_data.get('type') == 'list_reply':
                    content = f"List: {interactive_data.get('list_reply', {}).get('title', '')}"
                elif interactive_data.get('type') == 'button_reply':
                    content = f"Button: {interactive_data.get('button_reply', {}).get('title', '')}"
                else:
                    content = f"Interactive: {interactive_data.get('type', '')}"
            else:
                content = message_type
            print(f"[MESSAGE] From: {sender_phone[-4:]} | Type: {message_type} | Content: {content}")

            if message_id in PROCESSED_MESSAGE_IDS:
                print(f"[DUPLICATE] Message already processed: {message_id}")
                return "OK", 200

            PROCESSED_MESSAGE_IDS.append(message_id)
            thread = threading.Thread(target=process_message_in_background, args=(data,))
            thread.start()
        except Exception as e:
            print(f"[ERROR] Webhook handler: {e}")
        return "OK", 200
    else:
        return "Method Not Allowed", 405


# --- CHART GENERATION FUNCTION ---
def create_attendance_pie_chart(data, title):
    if not data or sum(data.values()) == 0:
        return None

    sorted_data = dict(sorted(data.items()))
    labels = sorted_data.keys()
    sizes = list(sorted_data.values())

    if 'Present' in labels and 'Absent' in labels:
        color_map = {'Present': '#2ECC71', 'Absent': '#E74C3C'}
        colors = [color_map[label] for label in labels]
    else:
        colors = sns.color_palette("viridis", len(labels)).as_hex()

    explode = tuple([0.03] * len(labels))
    fig, ax = plt.subplots(figsize=(10, 10), subplot_kw=dict(aspect="equal"))

    def autopct_format_absolute(values):
        def my_format(pct):
            total = sum(values)
            val = int(round(pct * total / 100.0))
            return f'{val}'
        return my_format

    wedges, texts, autotexts = ax.pie(
        sizes, labels=labels, colors=colors,
        autopct=autopct_format_absolute(sizes),
        startangle=90, pctdistance=0.8, explode=explode, shadow=True,
        wedgeprops=dict(width=0.4, edgecolor='w', linewidth=2),
        textprops={'fontsize': 16, 'weight': 'bold'}
    )

    plt.setp(autotexts, size=20, weight="bold", color='white')
    for autotext in autotexts:
        autotext.set_path_effects([path_effects.withStroke(linewidth=3, foreground='black')])

    plt.setp(texts, size=18, weight="bold", color='#363636')
    ax.set_title(title, fontsize=24, pad=25, weight='bold', color='#333333')

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150, transparent=True)
    buf.seek(0)
    plt.close(fig)
    return buf


# --- ROLE-BASED FLOW HANDLERS ---
def handle_executive_flow(phone, user_details, message_data):
    if message_data.get("type") == "interactive":
        interactive_data = message_data.get("interactive", {})
        selected_id = (interactive_data.get("button_reply") or interactive_data.get("list_reply", {})).get("id", "")
        if selected_id == 'exec_view_report':
            all_supervisors = get_all_supervisors()
            if not all_supervisors:
                send_text_message(phone, "No data available to generate a report.")
                return
            _, company_stats = get_ba_attendance_summary_for_supervisors([s['name'] for s in all_supervisors])
            company_chart_data = {'Present': company_stats.get('present', 0), 'Absent': company_stats.get('absent', 0)}
            image_buffer = create_attendance_pie_chart(company_chart_data, "NFL Attendance Report")
            team_leads = get_all_team_leads()
            text_breakdown = "🏢 *Company-Wide Attendance Summary*\n"
            for lead in team_leads:
                lead_supervisors = get_subordinates_by_role(lead['user_id'], 'Supervisor')
                if lead_supervisors:
                    _, lead_stats = get_ba_attendance_summary_for_supervisors([s['name'] for s in lead_supervisors])
                    present_count = lead_stats.get('present', 0)
                    absent_count = lead_stats.get('absent', 0)
                    text_breakdown += f"\n👨‍💼 *{lead['name']} ({lead['role']})*\n✅ Present: {present_count} | ❌ Absent: {absent_count}"
            send_chart_and_text_report(phone, image_buffer, text_breakdown)
            if team_leads:
                rows = [{"id": f"view_team-{lead['user_id']}", "title": lead['name'][:24]} for lead in team_leads]
                send_interactive_list_message(phone, "Drill Down", "Select a team lead to view their report.",
                                              "View Teams", [{"title": "Team Leads", "rows": rows}])
            return
        elif selected_id.startswith("view_team-"):
            pm_id = int(selected_id.split('-')[1])
            pm_details = get_user_details_by_id(pm_id)
            if pm_details: handle_pm_flow(phone, pm_details, {"type": "text"})
            return
        elif selected_id.startswith("view_sup-"):
            supervisor_id = int(selected_id.split('-')[1])
            supervisor_details = get_user_details_by_id(supervisor_id)
            if supervisor_details: handle_supervisor_flow(phone, supervisor_details, {"type": "text"})
            return
        elif selected_id.startswith("view_present-") or selected_id.startswith("view_absent-"):
            handle_view_ba_list(phone, selected_id)
            return
    rows = [{"id": "exec_view_report", "title": "View Attendance Report"}]
    send_interactive_list_message(phone, f"Welcome, {user_details['name']}", "Please select an option to get started.",
                                  "Main Menu", [{"title": "Options", "rows": rows}])


def handle_pm_flow(phone, user_details, message_data):
    if message_data.get("type") == "interactive":
        selected_id = (message_data.get("interactive", {}).get("list_reply") or message_data.get("interactive", {}).get(
            "button_reply", {})).get("id", "")
        if selected_id.startswith("view_sup-"):
            supervisor_id = int(selected_id.split('-')[1])
            supervisor_details = get_user_details_by_id(supervisor_id)
            if supervisor_details: handle_supervisor_flow(phone, supervisor_details, {"type": "text"})
            return
        elif selected_id.startswith("view_present-") or selected_id.startswith("view_absent-"):
            handle_view_ba_list(phone, selected_id)
            return
    pm_id = user_details['user_id']
    pm_name = user_details['name']
    supervisors = get_subordinates_by_role(pm_id, 'Supervisor')
    if not supervisors:
        send_text_message(phone, "You have no supervisors assigned to you.")
        return
    summary_text, team_stats = get_ba_attendance_summary_for_supervisors([s['name'] for s in supervisors])
    chart_data = {'Present': team_stats.get('present', 0), 'Absent': team_stats.get('absent', 0)}
    image_buffer = create_attendance_pie_chart(chart_data, f"Team Attendance for {pm_name}")
    text_breakdown = f"👨‍💼 *Team Report for {pm_name}*\n\n"
    text_breakdown += f"{summary_text}\n\n*Breakdown by Supervisor:*"
    for sup in supervisors:
        _, sup_stats = get_ba_attendance_summary_for_supervisors([sup['name']])
        present_count = sup_stats.get('present', 0)
        absent_count = sup_stats.get('absent', 0)
        text_breakdown += f"\n\n👤 *{sup['name']}*\n✅ Present: {present_count} | ❌ Absent: {absent_count}"
    send_chart_and_text_report(phone, image_buffer, text_breakdown)
    rows = [{"id": f"view_sup-{s['user_id']}", "title": s['name'][:24]} for s in supervisors]
    send_interactive_list_message(phone, "Drill Down", "Select a supervisor to view their report.", "View Supervisors",
                                  [{"title": "Supervisors", "rows": rows}])


def handle_supervisor_flow(phone, user_details, message_data):
    if message_data.get("type") == "interactive":
        selected_id = (message_data.get("interactive", {}).get("button_reply") or {}).get("id", "")
        if selected_id.startswith("view_present-") or selected_id.startswith("view_absent-"):
            handle_view_ba_list(phone, selected_id)
            return
    supervisor_id = user_details['user_id']
    supervisor_name = user_details['name']
    summary_text, stats = get_ba_attendance_summary_for_supervisors([supervisor_name])
    chart_data = {'Present': stats.get('present', 0), 'Absent': stats.get('absent', 0)}
    image_buffer = create_attendance_pie_chart(chart_data, f"Attendance for {supervisor_name}")
    send_chart_and_text_report(phone, image_buffer, f"📋 *Report for {supervisor_name}*\n\n" + summary_text)
    buttons = []
    if stats.get('present', 0) > 0:
        buttons.append({"id": f"view_present-{supervisor_id}", "title": "View Present BAs"})
    if stats.get('absent', 0) > 0:
        buttons.append({"id": f"view_absent-{supervisor_id}", "title": "View Absent BAs"})
    if buttons:
        send_interactive_button_message(phone, "Select an option to view names.", buttons)


def handle_view_ba_list(phone, selected_id):
    try:
        action, supervisor_id_str = selected_id.split('-', 1)
        supervisor_id = int(supervisor_id_str)
    except (ValueError, IndexError):
        send_text_message(phone, "❌ Invalid selection.")
        return
    supervisor_details = get_user_details_by_id(supervisor_id)
    if not supervisor_details:
        send_text_message(phone, "❌ Supervisor details not found.")
        return
    _, stats = get_ba_attendance_summary_for_supervisors([supervisor_details['name']])
    list_type = "Present" if action == "view_present" else "Absent"
    status_emoji = "✅" if list_type == "Present" else "❌"
    names_key = 'present_names' if list_type == "Present" else 'absent_names'
    ba_list = stats.get(names_key, [])
    if ba_list:
        sorted_ba_list = sorted(ba_list)
        message_parts = [f"{status_emoji} *{list_type} BAs for {supervisor_details['name']}:*"]
        for name, store in sorted_ba_list:
            message_parts.append(f"\n👤 *{name}*\n🏬 _{store}_")
        message = "\n".join(message_parts)
    else:
        message = f"No {list_type.lower()} BAs found for {supervisor_details['name']}."
    send_text_message(phone, message)


# --- DATABASE HELPERS ---
def get_user_details(phone_number):
    conn = None
    try:
        conn = pymysql.connect(**DB_HIERARCHY_CONFIG)
        with conn.cursor() as cursor:
            query = "SELECT user_id, name, role FROM user WHERE RIGHT(REPLACE(phone, '-', ''), 10) = RIGHT(%s, 10)"
            cursor.execute(query, (phone_number,))
            return cursor.fetchone()
    except pymysql.MySQLError as e:
        print(f"DB Error in get_user_details: {e}")
        return None
    finally:
        if conn: conn.close()


def get_user_details_by_id(user_id):
    conn = None
    try:
        conn = pymysql.connect(**DB_HIERARCHY_CONFIG)
        with conn.cursor() as cursor:
            cursor.execute("SELECT user_id, name, role, phone FROM user WHERE user_id = %s", (user_id,))
            return cursor.fetchone()
    finally:
        if conn: conn.close()


def get_subordinates_by_role(manager_id, role):
    conn = None
    try:
        conn = pymysql.connect(**DB_HIERARCHY_CONFIG)
        with conn.cursor() as cursor:
            cursor.execute("SELECT user_id, name, role FROM user WHERE manager_id = %s AND role = %s",
                           (manager_id, role))
            return cursor.fetchall() or []
    finally:
        if conn: conn.close()


def get_all_team_leads():
    conn = None
    try:
        conn = pymysql.connect(**DB_HIERARCHY_CONFIG)
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT DISTINCT m.user_id, m.name, m.role FROM user m JOIN user s ON m.user_id = s.manager_id WHERE s.role = 'Supervisor'")
            return cursor.fetchall() or []
    finally:
        if conn: conn.close()


def get_all_supervisors():
    conn = None
    try:
        conn = pymysql.connect(**DB_HIERARCHY_CONFIG)
        with conn.cursor() as cursor:
            cursor.execute("SELECT user_id, name, role FROM user WHERE role = 'Supervisor'")
            return cursor.fetchall() or []
    finally:
        if conn: conn.close()


def get_ba_attendance_summary_for_supervisors(supervisor_names):
    if not supervisor_names:
        return "No supervisors found.", {}
    conn = None
    try:
        conn = pymysql.connect(**DB_ATTENDANCE_CONFIG)
        with conn.cursor() as cursor:
            placeholders = ', '.join(['%s'] * len(supervisor_names))
            query = f"SELECT Supervisor, `BA Name`, `Store Name`, `BA Status` FROM V_NFL_BA_ATTENDANCE WHERE Supervisor IN ({placeholders}) AND `Date` = CURDATE()"
            cursor.execute(query, supervisor_names)
            todays_bas = cursor.fetchall() or []
        if not todays_bas:
            return "No BAs found assigned to the specified team(s) today.", {'present': 0, 'absent': 0,
                                                                             'present_names': [], 'absent_names': []}
        stats = {name: {'present': 0, 'absent': 0, 'present_names': [], 'absent_names': []} for name in
                 supervisor_names}
        for ba in todays_bas:
            supervisor = ba.get('Supervisor')
            if supervisor in stats:
                ba_name, store_name = ba.get('BA Name', 'Unknown'), ba.get('Store Name', 'N/A')
                if ba.get('BA Status') == 'Active':
                    stats[supervisor]['present'] += 1
                    stats[supervisor]['present_names'].append((ba_name, store_name))
                else:
                    stats[supervisor]['absent'] += 1
                    stats[supervisor]['absent_names'].append((ba_name, store_name))
        total_present = sum(s['present'] for s in stats.values())
        total_absent = sum(s['absent'] for s in stats.values())
        total_ba = total_present + total_absent
        percentage = (total_present / total_ba * 100) if total_ba > 0 else 0
        summary = f"✅ Present: *{total_present}*\n❌ Absent: *{total_absent}*\n👥 Total BAs: *{total_ba}*\n📊 Attendance Rate: *{percentage:.0f}%*"
        final_stats = {
            'present': total_present, 'absent': total_absent,
            'present_names': [n for s in stats.values() for n in s['present_names']],
            'absent_names': [n for s in stats.values() for n in s['absent_names']]
        }
        return summary.strip(), final_stats
    except pymysql.MySQLError as err:
        print(f"Attendance DB Error: {err}")
        return "Error fetching attendance data.", {}
    finally:
        if conn: conn.close()


# --- WHATSAPP MESSAGE SENDERS ---
def send_chart_and_text_report(phone, image_buffer, caption_text):
    if image_buffer:
        media_id = upload_whatsapp_media(image_buffer)
        if media_id:
            send_whatsapp_image_message(phone, media_id, caption_text)
        else:
            send_text_message(phone,
                              "⚠️ Could not generate the chart image. Here is the text summary:\n\n" + caption_text)
    else:
        send_text_message(phone, "⚠️ No data to generate a chart. Here is the text summary:\n\n" + caption_text)


def send_interactive_list_message(phone, header_text, body_text, button_text, sections):
    payload = {"messaging_product": "whatsapp", "to": phone, "type": "interactive",
               "interactive": {"type": "list", "header": {"type": "text", "text": header_text},
                               "body": {"text": body_text}, "action": {"button": button_text, "sections": sections}}}
    send_whatsapp_message(payload)


def send_interactive_button_message(phone, body, buttons):
    if not buttons: return
    payload = {"messaging_product": "whatsapp", "to": phone, "type": "interactive",
               "interactive": {"type": "button", "body": {"text": body},
                               "action": {"buttons": [{"type": "reply", "reply": b} for b in buttons[:3]]}}}
    send_whatsapp_message(payload)


def send_text_message(phone, message):
    payload = {"messaging_product": "whatsapp", "to": phone, "text": {"body": str(message)[:4096]}}
    send_whatsapp_message(payload)


def upload_whatsapp_media(image_buffer):
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/media"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    files = {'file': ('attendance.png', image_buffer, 'image/png'), 'messaging_product': (None, 'whatsapp')}
    try:
        response = requests.post(url, headers=headers, files=files, timeout=30)
        response.raise_for_status()
        media_id = response.json().get('id')
        print(f"Media uploaded successfully. ID: {media_id}")
        return media_id
    except requests.exceptions.RequestException as e:
        print(f"Error uploading media: {e}")
        if e.response: print(f"Response body: {e.response.text}")
        return None


def send_whatsapp_image_message(phone, media_id, caption=""):
    payload = {"messaging_product": "whatsapp", "to": phone, "type": "image",
               "image": {"id": media_id, "caption": caption}}
    send_whatsapp_message(payload)


def send_whatsapp_message(payload):
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        print(f"Error sending message: {e}")
        if e.response: print(f"Response body: {e.response.text}")
    except requests.exceptions.RequestException as e:
        print(f"Error sending message: {e}")


# --- HEALTH CHECK ENDPOINT ---
@app.route('/health', methods=['GET'])
def health_check():
    return {"status": "healthy", "timestamp": date.today().isoformat()}, 200


# --- RUN THE APP ---
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000, debug=False)