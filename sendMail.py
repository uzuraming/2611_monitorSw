# -*- coding: utf-8 -*-
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from os.path import basename

import sys
import subprocess
import os
import json
from datetime import datetime, timedelta

DESCRIPTION = "本メール配信サービスは株式会社S.E.Iが提供するものです。送信専用となりますので、返信は行わないでください。"
MAIL_LIST_PATH = "/opt/2611_monitorSw/mail_list.json"

def extract_json_after_keyword(text, keyword):
    # 指定されたキーワードがテキスト内に存在するか確認
    keyword_index = text.find(keyword)
    if keyword_index == -1:
        # キーワードが見つからない場合はNoneを返す
        return None

    # キーワード以降のテキストを取得
    json_text = text[keyword_index + len(keyword):]

    try:
        # JSON形式のテキストを辞書型に変換
        json_data = json.loads(json_text)
        return json_data
    except json.JSONDecodeError as e:
        # JSONデコードエラーが発生した場合はNoneを返す
        print("JSON decode error:", e)
        return None

def read_json(file_path):
    """JSONファイルを読み込む"""
    with open(file_path, "r", encoding="utf-8") as file:
        return json.load(file)

def get_tail(file_path, lines):
    """ファイルの末尾から指定された行数を取得"""
    with open(file_path, "r") as file:
        content = file.readlines()
    return "".join(content[-lines:])

def sendMail(to_emails, subject, body, alert_level=0):
    #発信元
    from_email = "monitor@illumina-lighting.com"

    #Create MIMEText
    msg = MIMEMultipart()
    if alert_level == 1:
        msg["Subject"]="『警告』" + subject
    else:
        msg["Subject"]=subject
    msg["To"]=",".join(to_emails)
    msg["From"]=from_email

    msg.attach(MIMEText(body, "html"))

    #SendMail by SMTP
    try:
        print("going to connect")
        server = smtplib.SMTP_SSL("sv2222.xserver.jp",465) # SMTP_SSL for x server
        print("smtp_ssl connection")
        #server.set_debuglevel(True) #debug mode
        #server.starttls()
        x = server.login("osasys@zigenls.com","OSAsys2020") #login
        print(x) #success if Authorization successful
        #send mail to multiple people
        server.send_message(msg)
        #close
        server.quit()
        print(f"メールを送信しました: {to_emails}")
    except Exception as e:
        print(f"メール送信中にエラーが発生しました: {e}")

def create_html_body(alert_level=0):
    # メール送信対象：bodyのメッセージを作成

    email_body = "<html><body>"

    # 最新の状態が正常かどうかを記述
    email_body += "<p>--- TIMEOUT ---</p><p>"
    email_body += get_html_tail(FAILCHECK_RECORD_PATH, 1)
    email_body += "</p><br>"

    # log_listからファイル内容を取得してメール送信
    log_contents = []
    for log in data["log_list"]:
        try:
            content = get_html_tail(log["file_path"], log["tail"])
            log_contents.append(f"--- {log['name']} ---</p><p>{content}")
            if log["delete"] == True:
               delete_file_contents(log["file_path"]) 
        except Exception as e:
            log_contents.append(f"--- {log['name']} ---\n取得中にエラー: {e}")

    email_body += '<p>'
    email_body += '</p><p>'.join(log_contents)
    email_body += '</p>'

    email_body += '<p>' + DESCRIPTION + '</p>'

    email_body += "</body></html>"
    print(email_body)

    return email_body

def create_body():
    # メール送信対象：bodyのメッセージを作成
    # Get Gloal IP
    import requests
    res = requests.get('http://inet-ip.info/ip')

    #message = "global ip\n" + res.text + "\n"
    email_body = ""

    email_body += "\n\n"

    # log_listからファイル内容を取得してメール送信
    log_contents = []
    for log in data["log_list"]:
        try:
            content = get_tail(log["file_path"], log["tail"])
            log_contents.append(f"--- {log['name']} ---\n{content}")
            if log["delete"] == True:
               delete_file_contents(log["file_path"]) 
        except Exception as e:
            log_contents.append(f"--- {log['name']} ---\n取得中にエラー: {e}")

    # メール本文を作成
    email_body += '\n'.join(log_contents)

    email_body += DESCRIPTION
    print(email_body)

    return email_body

def scheduled_send_mail():
    """現在時刻を確認し、必要ならメールを送信"""
    data = read_json(MAIL_LIST_PATH)
    now = datetime.now()

    # メール送信対象時間の判定
    for mail_time in data["mail_time"]:
        target_time = datetime.strptime(mail_time, "%H:%M").replace(year=now.year, month=now.month, day=now.day)
        if is_same_minute(now, target_time):

            print(f"time to send email at {target_time}")
            sendMail(data["to_mail"], SUBJECT, create_html_body(), 0)
            break  # 1回送信したら終了
    # 
    print("no schedule to send email")

if __name__ == '__main__':
    data = read_json(MAIL_LIST_PATH)

    if len(sys.argv) > 1 and sys.argv[1] == "scheduled_mail":
        scheduled_send_mail()

    else:
        if len(sys.argv) > 1:
            alert_level = int(sys.argv[1])
        else:
            alert_level = 0
    
        sendMail(data["to_mail"], SUBJECT, create_html_body(), alert_level)
