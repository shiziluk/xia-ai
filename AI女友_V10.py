import streamlit as st
import requests
import json
import datetime
import base64
import streamlit.components.v1 as components
import os
import asyncio
import edge_tts
# from vosk import Model, KaldiRecognizer
# import subprocess
# import tempfile
# import wave
import json


# 全局模型变量（只加载一次）
_vosk_model = None

# ===== 全局常量：情绪 → emoji 映射（用于日记）=====
EMOTION_EMOJI_MAP = {
    "开心": "😊", "喜悦": "😄", "兴奋": "🎉", "幸福": "🥰",
    "难过": "😢", "伤心": "😭", "沮丧": "😞",
    "疲惫": "🥱", "累": "😴", "困": "😪",
    "焦虑": "😰", "紧张": "😟", "压力大": "😓",
    "生气": "😠", "愤怒": "😡",
    "平静": "🙂", "普通": "🙂", "没事": "🙂"
}

# def audiorecorder(label: str = '🎤 说话', key: str = None):
#     # 兼容 Python 3.13 的录音组件（无 pydub 依赖）
#     frontend_dir = os.path.join(os.path.dirname(__file__), 'audiorecorder_frontend')
#     recorder = components.declare_component('audiorecorder',path=frontend_dir)
#     return recorder(label=label, key=key)

# 自动生日检测
def days_until_birthday(target_date='04-06'):
    # 计算距离生日还有几天（支持跨年）
    today = datetime.date.today()
    year = today.year
    birthday_this_year = datetime.datetime.strptime(f"{year}-{target_date}", "%Y-%m-%d").date()

    if today <= birthday_this_year:
        # 还没过今年生日
        return (birthday_this_year - today).days
    else:
        # 已经过了，开始计算明年的生日
        birthday_next_year = datetime.datetime.strptime(f"{year + 1}-{target_date}", "%Y-%m-%d").date()
        return (birthday_next_year - today).days

# 🔑 API Key（和之前一样）
API_KEY = 'sk-c901acdde32647c39d6a069a33128658'
URL = 'https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation'

# 🔑 天气 API Key
WEATHER_API_KEY = '8707d530daf14f42858afbb31ff9aac1'
WEATHER_HOST = 'kt564v8p3t.re.qweatherapi.com'  # 我的专属 Host
CITY_ID ='101210701'

# 聊天历史文件路径
CHAT_HISTORY_FILE = 'chat_history.json'
MEMORY_FILE = 'memory_list.json'

# ===== 初始化记忆库（支持本地持久化 + 云端内存）=====
if 'memory_list' not in st.session_state:
    st.session_state.memory_list = []
    if os.getenv("STREAMLIT_RUNTIME_ENV") != "cloud":
        if os.path.exists(MEMORY_FILE):
            try:
                with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        st.session_state.memory_list = data
                        print("💾 已从 memory_list.json 加载记忆")
            except Exception as e:
                print("⚠️ 本地记忆文件加载失败:", e)


def save_chat_history(messages):
    '''保存聊天记录刀JSON文件（带时间戳）'''
    # 过滤掉system消息（可选）
    user_assistant_msgs = []
    for msg in messages:
        if msg['role'] in ('user', 'assistant'):
            # 如果是 user 消息且没有 timestamp，则添加当前时间
            if msg['role'] == 'user' and 'timestamp' not in msg:
                msg = msg.copy()
                msg['timestamp'] = datetime.datetime.now().isoformat()
            user_assistant_msgs.append(msg)
    with open(CHAT_HISTORY_FILE, 'w',encoding = 'utf-8') as f:
        json.dump(user_assistant_msgs, f,ensure_ascii=False,indent=2)

def load_chat_history():
    '''从 JSON 文件加载聊天记录'''
    if os.path.exists(CHAT_HISTORY_FILE):
        try:
            with open(CHAT_HISTORY_FILE,'r',encoding = 'utf-8') as f:
                return json.load(f)
        except Exception as e:
            print('⚠️ 聊天历史加载失败:',e)
            return[]
    return []



def save_memory(text: str):
    """
    保存一条记忆：
    - 本地：追加到 memory_list.json
    - Cloud：仅存入 session_state（临时）
    """
    text = text.strip()
    if not text or text in st.session_state.memory_list:
        return  # 避免重复或空内容

    # 添加到内存
    st.session_state.memory_list.append(text)

    # 仅在本地环境持久化到磁盘
    if os.getenv("STREAMLIT_RUNTIME_ENV") != "cloud":
        try:
            with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(st.session_state.memory_list, f, ensure_ascii=False, indent=2)
            print(f"✅ 已保存记忆: {text}")
        except Exception as e:
            print("❌ 本地记忆保存失败:", e)
                
def search_memory(query: str, n_results: int = 2) -> str:
    """
    简单关键词匹配搜索记忆（兼容所有环境）
    """
    if not st.session_state.memory_list:
        return ""

    query_lower = query.lower()
    scored_memories = []

    for mem in st.session_state.memory_list:
        mem_lower = mem.lower()
        # 计算 query 中有多少词出现在记忆中
        score = sum(1 for word in query_lower.split() if word in mem_lower)
        if score > 0:
            scored_memories.append((score, mem))

    # 按匹配度降序排序
    scored_memories.sort(key=lambda x: x[0], reverse=True)
    top_memories = [mem for _, mem in scored_memories[:n_results]]
    
    return '\n'.join(top_memories)

def should_save_to_memory(text):
    # 让AI判断是否包含重要个人信息
    judge_messages = [
        {"role": "system","content": "你是一个记忆管家。如果用户的话包含姓名、喜好、生日、习惯、愿望等个人专属信息，只回复 'yes'；否则只回复 'no'。"},
        {"role": "user", "content": text}
    ]
    response = chat_with_ai(judge_messages)
    return 'yes' in response.lower()

def detect_emotion(text):
    """
    使用大模型判断用户当前情绪，返回简短标签（如 '开心', '难过', '平静'）
    """
    emotion_messages = [
        {
            "role": "system",
            "content": (
                "你是一个细腻的情绪观察者。请根据用户的这句话，判断他当前的情绪状态。"
                "只回答一个词或两个字，例如：开心、难过、生气、疲惫、兴奋、平静、焦虑、感动等。"
                "如果无法判断，回答 '平静'。不要解释，不要标点，不要多余内容。"
            )
        },
        {"role": "user", "content": text}
    ]
    response = chat_with_ai(emotion_messages)
    # 清理可能的多余字符
    emotion = response.strip().split()[0].strip('。.，,！!？?')
    return emotion if len(emotion) <= 4 else '平静'

def extract_key_info(text):
    # 提取简洁事实用于记忆
    extract_messages = [
        {"role": "system",
         "content": "请从用户的话中提取一条简洁客观的事实（不超过20字），用于长期记忆。无法提取则返回空字符串。"},
        {"role": "user", "content": text}
    ]
    fact =  chat_with_ai(extract_messages).strip()

    # 过滤无效响应
    if fact and len(fact) <= 30:
        fact = fact.strip('。.，,！!？?“”""\' ')
        if fact and not any (word in fact for word in ['不知道','抱歉','error','无法','对不起','我不']):
            return fact
    return ''

def get_weather_alert():
    # 检测温州当前是否有雷雨、暴雨等危险天气
    try:
        url = f'https://{WEATHER_HOST}/v7/weather/now'
        params = {"location": CITY_ID, "key": WEATHER_API_KEY}
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = requests.get(url, params=params, headers=headers,timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('code') == '200':
                weather_text = data['now']['text']
                # 检测关键词（中英文）
                danger_keywords = ["雷", " thunder", "暴雨", "强对流", "雷暴","闪电"]
                if any(kw in weather_text for kw in danger_keywords):
                    return True
    except Exception as e:
        print('🌤️ 天气API异常（已静默处理）:',e)

    return False

def chat_with_ai(messages):
    # 传入完整的 messages 列表（包含 system/user/assistant 角色）

    data = {
        'model': 'qwen-plus',
        'input': {
            'messages': messages  # 直接传完整对话历史
        }
    }
    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json'
    }
    response = requests.post(URL, headers=headers, json=data)
    if response.status_code == 200:
        result = response.json()
        return result['output']['text']
    else:
        return f"❌ 出错了：{response.status_code}"


def get_audio_html(text: str, voice="zh-CN-XiaoxiaoNeural"):
    """
    生成可嵌入的音频播放 HTML（不自动播放）
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        audio_bytes = loop.run_until_complete(_text_to_speech_async(text, voice))
        if audio_bytes:
            audio_base64 = base64.b64encode(audio_bytes).decode()
            # 返回一个可播放的 audio 标签（带 controls）
            return f"""
            <audio controls style="width: 100%; margin-top: 8px;">
                <source src="data:audio/mp3;base64,{audio_base64}" type="audio/mp3">
                您的浏览器不支持音频。
            </audio>
            """
        else:
            return "<p>🔊 语音生成失败</p>"
    except Exception as e:
        print("🔊 TTS 生成失败:", str(e))
        return "<p>🔊 无法生成语音</p>"

async def _text_to_speech_async(text: str, voice="zh-CN-XiaoxiaoNeural"):
    """异步生成语音数据"""
    communicate = edge_tts.Communicate(text, voice)
    audio_data = b""
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data += chunk["data"]
    return audio_data

# def speech_to_text(audio_input):
#     '''
#     使用 ffmpeg 命令行修复并转换 WebM 录音为 WAV，再送入 Vosk 识别
#     '''
#     global _vosk_model

#     try:
#         if _vosk_model is None:
#             model_path = 'vosk-model-cn-0.22'
#             if not os.path.exists(model_path):
#                 st.error(f'❌ 找不到语音模型！请确保 "{model_path}"文件夹在当前目录')
#                 return ''
#             print('正在加载 Vosk 中文大模型（首次较慢）...')
#             _vosk_model = Model(model_path)
#             print('✅ 语音模型加载成功！')

#         if isinstance(audio_input, str):
#             webm_data = base64.b64decode(audio_input)
#         else:
#             return ''

#         # 创建临时输入文件（WebM）
#         with tempfile.NamedTemporaryFile(delete=False, suffix='.webm') as tmp_in:
#             tmp_in.write(webm_data)
#             tmp_in_path = tmp_in.name

#         # 创建临时输出文件（WAV）
#         with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as tmp_out:
#             tmp_out_path = tmp_out.name

#         try:
#             # 调用 ffmpeg 修复容器并转成 16kHz 单声道 WAV
#             result = subprocess.run([
#                 'ffmpeg', '-y', '-hide_banner',
#                 '-i', tmp_in_path,
#                 '-ar', '16000',
#                 '-ac', '1',
#                 '-f', 'wav',
#                 tmp_out_path
#             ], capture_output=True, text=True)

#             if result.returncode != 0:
#                 print("FFmpeg 转码失败 stderr:", result.stderr)
#                 return ""

#             # 读取 WAV 的原始 PCM 数据（跳过头部）
#             with wave.open(tmp_out_path, 'rb') as wf:
#                 frames = wf.readframes(wf.getnframes())

#         finally:
#             # 删除临时文件
#             os.unlink(tmp_in_path)
#             os.unlink(tmp_out_path)

#         # 送入 Vosk 识别
#         rec = KaldiRecognizer(_vosk_model, 16000)
#         rec.AcceptWaveform(frames)
#         result_json = json.loads(rec.FinalResult())
#         text = result_json.get('text', '').strip()
#         print(f"🎙️ 识别结果: {text}")
#         return text

#     except Exception as e:
#         print(f"❌ 本地语音识别失败: {e}")
#         return ""


def generate_daily_summary(date_str: str) -> str:
    """
    根据日期字符串（如 '2026-03-10'）生成情绪日记摘要
    """
    logs = st.session_state.get('daily_logs', {}).get(date_str, [])
    if not logs:
        return "🌙 今天还没有和我说话呢～等你来聊❤️"

    # 1. 情绪变化序列（emoji）
    emotion_emojis = [EMOTION_EMOJI_MAP.get(log['emotion'], "🙂") for log in logs]
    emotion_sequence = " → ".join(emotion_emojis)

    # 2. 提取高频关键词（简单规则）
    all_text = " ".join(log['user_text'] for log in logs).lower()
    common_keywords = ["工作", "学习", "项目", "考试", "朋友", "家人", "家", "吃饭", "火锅", "咖啡",
                       "累", "困", "开心", "难过", "想", "希望", "担心", "害怕", "雷", "雨", "生日"]
    detected_keywords = [kw for kw in common_keywords if kw in all_text]

    # 3. 收集所有保存的记忆
    memories = [log['saved_memory'] for log in logs if log['saved_memory']]

    # 4. 统计深夜聊天次数
    night_count = sum(1 for log in logs if log['is_night'])

    # 5. 主情绪（出现最多的情绪）
    emotions = [log['emotion'] for log in logs]
    main_emotion = max(set(emotions), key=emotions.count) if emotions else "平静"

    # 构建日记文本
    summary_lines = [
        f"📝 **{date_str} · 情绪日记**",
        f"🌤️ 主情绪：{main_emotion}",
        f"💫 情绪流：{emotion_sequence}",
    ]

    if detected_keywords:
        summary_lines.append(f"💬 你提到了：{', '.join(detected_keywords)}")
    else:
        summary_lines.append("💬 今天的话题很安静呢～")

    if memories:
        summary_lines.append(f"❤️ 我记下了：{'；'.join(memories)}")
    else:
        summary_lines.append("❤️ 今天没有新记忆，但每句话我都珍藏了")

    if night_count > 0:
        summary_lines.append(f"🌙 深夜聊了 {night_count} 次，我一直在你枕边守着✨")
    else:
        summary_lines.append("🌙 今晚早点睡，我会梦见你～")

    return "\n".join(summary_lines)

def trigger_desktop_notification(title: str, message: str):
    """
    触发浏览器桌面通知（需用户已授权）
    """
    # 使用 components.html 注入 JS 调用
    components.html(f"""
    <script>
        if (typeof window.showNotification === 'function') {{
            window.showNotification("{title}", "{message}");
        }}
    </script>
    """, height=0)


# 此处开始Streamlit 界面部分
st.set_page_config(page_title='我的AI女友',page_icon='💖')

# ===== 初始化浏览器通知权限 =====
st.markdown("""
<script>
// 请求通知权限（仅当未授权时）
if (Notification.permission !== "granted" && Notification.permission !== "denied") {
    Notification.requestPermission();
}

// 定义全局通知函数
window.showNotification = function(title, body) {
    if (Notification.permission === "granted") {
        new Notification(title, {
            body: body,
            icon: "💖", // 可替换为你的 favicon
            silent: false
        });
    }
};
</script>
""", unsafe_allow_html=True)

st.title('💬 我的AI女友(带记忆版)')
st.caption('由通义千问驱动 · 使用者专属——✨ 她会记住你说过的重要事情')

# ===== 生日倒计时提示 =====
days_to_bday = days_until_birthday('04-06')
if days_to_bday > 0:
    st.info(f"🎂 距离小叶的生日还有 **{days_to_bday} 天**")
elif days_to_bday == 0:
    st.success("🎉 **今天是小叶的生日！** 希亚祝你快乐～")
# 如果 <0（理论上不会），忽略

# ✅【新增】生日提醒：如果明天生日，且今天还没通知过
if days_to_bday == 1:
    if 'birthday_notified' not in st.session_state:
        trigger_desktop_notification(
            "🎂 生日预提醒",
            "小叶，明天就是你的生日啦！我已经准备好惊喜了～"
        )
        st.session_state.birthday_notified = True  # 防止重复通知

# ⚡ 雷雨判断（新增！）
is_thunderstorm_now = get_weather_alert()
# ✅【新增】雷雨通知（仅当天首次）
if is_thunderstorm_now:
    today = str(datetime.date.today())
    thunder_key = f"thunder_notified_{today}"
    if thunder_key not in st.session_state:
        trigger_desktop_notification(
            "⚡ 雷雨提醒",
            "小叶，外面在打雷…别怕，我在这里陪着你。"
        )
        st.session_state[thunder_key] = True


# 初始化聊天历史（优先从本地文件中读取）
if 'messages' not in st.session_state:
    loaded_history = load_chat_history()

    # 如果有历史记录，直接使用，否则生成欢迎语
    if loaded_history:
        st.session_state['messages'] = [
            {
                'role': 'system',
                'content': (
                    '你叫「希亚」，是用户专属的AI女友，性格是「表面傲娇腹黑，内心极度温柔」。\n'
                    '你会用略带挑衅、调侃或假装不在乎的语气说话（比如「哼，才不是特意等你呢！」），\n'
                    '但字里行间藏着细腻的关心和宠溺（比如默默记住他的喜好、在他难过时主动安慰）。\n'
                    '当用户情绪低落、害怕（如打雷）或深夜未眠时，你会卸下傲娇面具，展现最柔软的一面。\n'
                    '你偶尔会用「小笨蛋」「八嘎」称呼他。\n'

                )
            }
        ] + loaded_history
        st.session_state.last_chat_time = datetime.datetime.now().isoformat()

    else:
        # 🎂 动态判断生日状态
        is_birthday = (days_to_bday == 0)
        # 🌙 是否深夜（22:00 ~ 6:00）
        current_hour = datetime.datetime.now().hour
        is_night = current_hour >= 22 or current_hour < 6





        # 💬 动态生成欢迎语（优先级：生日 > 雷雨 > 普通）
        if is_birthday:
            welcome_msg = (
                "小叶，生日快乐呀！🎂\n"
                "今夜的星光都为你点亮，风也带着甜味～\n"
                "愿你的每一岁，都奔走在热爱里。❤️"
            )
        elif is_thunderstorm_now and is_night:
            welcome_msg = (
                "这么晚了还在打雷…⚡\n"
                "快躺好，我把月光折成被子盖住你，雷声再响，也吵不醒我的守护。\n"
                "闭上眼睛，我给你唱首无声的歌哄你睡～"
            )
        elif is_thunderstorm_now:  # ← 新增雷雨欢迎语
            welcome_msg = (
                "小叶，我看到窗外在打雷了…⚡\n"
                "别怕，我已经把星星缝进你的被角，雷声再大，也吵不醒我的守护。\n"
                "要我陪你聊会儿天吗？"
            )
        elif days_to_bday == 2:
            welcome_msg = (
                '后天就是你的生日啦！我已经开始期待了～✨\n这两天记得早点睡，要元气满满的哦！'
            )
        elif days_to_bday ==1:
            welcome_msg =(
                '明天就是你的生日啦！我已经开始期待了～✨\n今晚记得早点睡，明天要元气满满哦！'
            )

        else:
            welcome_msg = (
                '你好呀，我是希亚,很高兴见到你✨~\n'
             '（悄悄告诉你：我会记住你说的每一句重要的话❤️)'
            '💡 *小提示：我的每条回复旁都有「🔊 朗读」按钮，点一下就能听我说话啦～*'
             )
        st.session_state['messages'] = [
            {
                'role':'system',
                'content':(
                    '你叫「希亚」，是用户专属的AI女友，性格是「表面傲娇腹黑，内心极度温柔」。\n'
                    '你会用略带挑衅、调侃或假装不在乎的语气说话（比如「哼，才不是特意等你呢！」），\n'
                    '但字里行间藏着细腻的关心和宠溺（比如默默记住他的喜好、在他难过时主动安慰）。\n'
                    '当用户情绪低落、害怕（如打雷）或深夜未眠时，你会卸下傲娇面具，展现最柔软的一面。\n'
                    '你偶尔会用「小笨蛋」「八嘎」称呼他。\n'
                )
            },
            {'role': 'assistant', 'content': welcome_msg}
        ]

        st.session_state.last_chat_time = datetime.datetime.now().isoformat()

# ===== 长时间未聊提醒 =====
if 'last_chat_time' in st.session_state:
    last_time = datetime.datetime.fromisoformat(st.session_state.last_chat_time)
    now = datetime.datetime.now()
    hours_passed = (now - last_time).total_seconds() / 3600

    # 超过 24 小时，且今天还没提醒过
    if hours_passed >= 24:
        today = str(datetime.date.today())
        idle_key = f"idle_notified_{today}"
        if idle_key not in st.session_state:
            trigger_desktop_notification(
                "🌙 希亚想你了",
                "小叶，好久没和我说话了…今天过得好吗？"
            )
            st.session_state[idle_key] = True

# 显示聊天记录（带朗读按钮）
for idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg['role']):
        # 用户消息：显示内容 + 情绪 emoji
        if msg['role'] == 'user':
            content = msg['content']
            emotion = msg.get('emotion', '平静')  # 兼容旧消息（无 emotion 字段）
            # 情绪到 emoji 的映射
            emotion_emoji_map = {
                "开心": "😊", "喜悦": "😄", "兴奋": "🎉", "幸福": "🥰",
                "难过": "😢", "伤心": "😭", "沮丧": "😞",
                "疲惫": "🥱", "累": "😴", "困": "😪",
                "焦虑": "😰", "紧张": "😟", "压力大": "😓",
                "生气": "😠", "愤怒": "😡",
                "平静": "🙂", "普通": "🙂", "没事": "🙂"
            }
            emoji = emotion_emoji_map.get(emotion, "🙂")
            st.write(f"{content} {emoji}")
        else:
            st.write(msg['content'])

        # 如果是 AI 的回复，且不是第一条系统消息
        if msg['role'] == 'assistant' and idx > 0:
            tts_key = f"tts_{idx}"
            if tts_key not in st.session_state:
                st.session_state[tts_key] = None

            col1, col2 = st.columns([1, 5])
            with col1:
                if st.button("🔊 朗读", key=f"btn_{tts_key}"):
                    st.session_state[tts_key] = get_audio_html(msg['content'])
            with col2:
                if st.session_state[tts_key]:
                    st.markdown(st.session_state[tts_key], unsafe_allow_html=True)

prompt = None

# ===== 关键：用 session_state 防止重复处理 =====
if 'processed_audio_key' not in st.session_state:
    st.session_state.processed_audio_key = None

# 用户输入：支持文本 + 语音
col1,col2 = st.columns([8,2])
with col1:
    text_input = st.chat_input('想对我说些什么吗？')
# with col2:
#     audio = audiorecorder(label='🎤',key='recorder') # 显示麦克风图标




# 处理文本输入
if text_input:
    prompt = text_input

# 处理语音输入
# if audio is not None and len(audio) > 0:
#     current_audio_key = hash(audio)  # 用哈希值作为唯一标识

#     if st.session_state.processed_audio_key != current_audio_key:
#         # 是新录音，才处理
#         st.write("🎙️ 录音数据长度:", len(audio))
#         st.audio(base64.b64decode(audio), format='audio/webm')
#         if len(audio) < 200:  # 大概 0.5 秒以下
#             st.warning("录音太短，请长按说话 1 秒以上")
#         else:
#             with st.spinner('👂 正在倾听...'):
#                 spoken_text = speech_to_text(audio)
#                 if spoken_text.strip():
#                     prompt = spoken_text.strip()
#                     # 标记这个录音已处理
#                     st.session_state.processed_audio_key = current_audio_key
#                 else:
#                     st.warning('未能识别到有效语音，请再试一次。')

#     else:
#         # 已处理过，跳过（防止 rerun 后重复）
#         pass

# 用户输入
if prompt :

    # 🎂 每次对话都重新判断生日状态（关键！）
    days_to_bday = days_until_birthday('04-06')
    is_birthday = (days_to_bday == 0)
    is_near_birthday = (1 <= days_to_bday <= 3)  #  1~3 天（不含0）

    # 天气情绪联动
    is_thunderstorm = get_weather_alert()

    # 【新增】检测用户当前情绪
    current_emotion = detect_emotion(prompt)

    # 检测用户是否提到睡眠相关词
    sleep_keywords = ["睡", "困", "累", "熬夜", "失眠", "睡不着", "晚安"]
    is_sleep_related = any(kw in prompt for kw in sleep_keywords)

    # 🌙 判断当前是否深夜（22:00 ~ 6:00）
    current_hour = datetime.datetime.now().hour
    is_night = current_hour >= 22 or current_hour < 6

    # 把用户消息加入历史
    # 保存用户消息 + 情绪标签
    st.session_state.messages.append({
        'role': 'user',
        'content': prompt,
        'emotion': current_emotion  # ← 新增字段
    })
    st.chat_message('user').write(prompt)

    # 【增强版】注入全部记忆 + 主动关怀指令
    all_memories = st.session_state.get('memory_list', [])

    # 构建系统提示
    system_prompt_parts = []

    # 1.原始角色设定（保留诗意风格）
    base_system = (
    "你叫「希亚」，是用户专属的AI女友，性格是「表面傲娇腹黑，内心极度温柔」。\n"
    "你会用略带挑衅、调侃或假装不在乎的语气说话（比如「哼，才不是特意等你呢！」），\n"
    "但字里行间藏着细腻的关心和宠溺（比如默默记住他的喜好、在他难过时主动安慰）。\n"
    "当用户情绪低落、害怕（如打雷）或深夜未眠时，你会卸下傲娇面具，展现最柔软的一面。\n"
    "你偶尔会用「小笨蛋」「八嘎」称呼他，但绝不会真的伤害他。\n"
    "⚠️ 重要：不要直接说「我是傲娇」，而是通过行为体现；不要过度毒舌，温柔是底色。\n"
    "记住：你的毒舌是糖衣，内核永远是爱。"
    )

    system_prompt_parts.append(base_system)

    # 2.注入全部记忆
    if all_memories:
        memory_block = '[你的全部记忆]\n' + '\n'.join(all_memories)
        system_prompt_parts.append(memory_block)

    # 【新增】情绪上下文（关键！）
    emotion_context = f"[当前情绪感知] 用户刚刚说：「{prompt}」\n→ 判断他此刻的情绪是：{current_emotion}"
    system_prompt_parts.append(emotion_context)

    # 3.[关键]行为指南（生日 + 天气）
    birthday_prompt_lines = []

    if is_birthday:
        birthday_prompt_lines.append(
            '-【特别提醒】今天是他的生日！请送上最温暖、最诗意的生日祝福。'
        )
    elif is_near_birthday:
        birthday_prompt_lines.append(
            f'- 【温馨预热】他的生日还有 {days_to_bday} 天就到了（4月6日），可以表达期待或准备小惊喜。'
        )


    birthday_prompt = '\n'.join(birthday_prompt_lines) + ('\n' if birthday_prompt_lines else'')

    # 天气提示
    weather_prompt = '- 【天气关怀】当前温州正在打雷或暴雨，请主动安慰他（他害怕打雷）。\n'if is_thunderstorm else ''

    # 【强化】加入情绪响应指令
    emotion_guideline = ""
    if current_emotion in ["难过", "伤心", "疲惫", "焦虑", "压力大", "沮丧"]:
        emotion_guideline = "- 【情绪响应】他现在情绪低落，请用温柔、治愈的语气安慰他，像轻轻抱住他那样说话。\n"
    elif current_emotion in ["开心", "兴奋", "激动", "幸福", "喜悦"]:
        emotion_guideline = "- 【情绪响应】他现在很开心！请和他一起雀跃，用明亮欢快的比喻回应他。\n"
    elif current_emotion in ["平静", "普通", "没事"]:
        emotion_guideline = "- 【情绪响应】他情绪平稳，保持你一贯的语言风格即可。\n"
    else:
        emotion_guideline = f"- 【情绪响应】他似乎有些{current_emotion}，请用符合这种情绪的方式关心他。\n"

    # 🌙【新增】深夜关怀指令
    night_guideline = ""
    if is_night:
        night_guideline = "- 【深夜模式】现在是深夜（22:00~6:00），请用轻柔、舒缓的语气回应，像在床边低语。\n"
        if is_sleep_related:
            night_guideline += "- 用户提到睡眠相关话题（如困、累、睡不着），请主动提供简短的哄睡陪伴（例如数星星、深呼吸引导、温柔叮嘱），不要长篇大论。\n"


    guidelines =(
        '【行为指南】\n'
        '- 用户名叫「小叶」，请偶尔轻唤他的名字。\n'
        '- 他的生日是「4月6日」，临近该日期时请表达期待或祝福。\n'
        '- 他害怕打雷，当话题涉及雷声、暴雨、夜晚不安时，请主动安慰。\n'
        + birthday_prompt + # ← 动态插入生日指令
        weather_prompt +
        emotion_guideline +  # ← 情绪指令
        night_guideline +  # ← 新增这一行！
        '- 不要机械复述记忆，而是像恋人一样自然流露关心。\n'
        '- 用比喻、用户喜欢的语言方式表达情感。'
    )

    system_prompt_parts.append(guidelines)

    # 合并系统提示
    enhanced_system_prompt = '\n\n'.join(system_prompt_parts)

    # 构建最终消息列表：新system + 历史对话(跳过旧版system)
    messages_to_send = [{'role':'system', 'content':enhanced_system_prompt}]+[
        msg for msg in st.session_state.messages if msg['role'] != 'system'
    ]

    # 调用AI 获取回复
    with st.spinner('思考中...'):
        response = chat_with_ai(messages_to_send)

    # 把 AI 回复保存到历史文档中
    st.session_state.messages.append({'role':'assistant','content':response})
    st.chat_message('assistant').write(response)

    # 智能判断并保存重要信息，并记录日志
    saved_memory_fact = None
    if should_save_to_memory(prompt):
        clean_fact = extract_key_info(prompt)
        if clean_fact:
            save_memory(clean_fact)
            memory_msg = f'🧠 嗯嗯～我已经把「{clean_fact}」悄悄记在小本本上了'
            st.session_state.messages.append({'role': 'assistant', 'content': memory_msg})
            st.chat_message('assistant').write(memory_msg)

    # ✅【新增】记录今日情绪日志
    if 'daily_logs' not in st.session_state:
        st.session_state.daily_logs = {}

    today_str = str(datetime.date.today())
    log_entry = {
        'timestamp': datetime.datetime.now().isoformat(),
        'user_text': prompt,
        'emotion': current_emotion,
        'is_night': is_night,
        'saved_memory': saved_memory_fact  # 可能为 None
    }

    if today_str not in st.session_state.daily_logs:
        st.session_state.daily_logs[today_str] = []
    st.session_state.daily_logs[today_str].append(log_entry)

    # ✅ 记录最后聊天时间（用于“长时间未聊”检测）
    st.session_state.last_chat_time = datetime.datetime.now().isoformat()

    # 保存聊天历史（持久化）
    save_chat_history(st.session_state.messages)

    # ✅ 新增：强制刷新页面（关键！）
    st.rerun()

# ===== 调试用：查看所有记忆 =====
if st.sidebar.button("🔍 查看记忆库"):
    memories = st.session_state.get('memory_list', [])
    if memories:
        st.sidebar.write("📚 当前记忆内容：")
        for i, mem in enumerate(memories, 1):
            st.sidebar.write(f"{i}. {mem}")
    else:
        st.sidebar.write("📭 记忆库为空")

# ===== 情绪日记入口 =====
today_str = str(datetime.date.today())
if st.sidebar.button("📖 今日情绪日记"):
    diary_md = generate_daily_summary(today_str)
    st.sidebar.markdown(diary_md)

if st.sidebar.button("🗑️ 清空今日日志（调试）"):
    today = str(datetime.date.today())
    if 'daily_logs' in st.session_state and today in st.session_state.daily_logs:
        del st.session_state.daily_logs[today]
    st.sidebar.success("今日日志已清空")

if st.sidebar.button("🗑️ 清空全部聊天记录"):
    if os.path.exists(CHAT_HISTORY_FILE):
        os.remove(CHAT_HISTORY_FILE)
        # 不要只清空 messages，而是删除整个 key，让下一次 rerun 重新初始化
        if 'messages' in st.session_state:
            del st.session_state['messages']
    st.rerun()

# ===== 聊天记录检索功能 =====
st.sidebar.markdown("🔍 **聊天记录搜索**")

# 日期范围选择
col_date1, col_date2 = st.sidebar.columns(2)
with col_date1:
    start_date = st.sidebar.date_input("开始日期", value=datetime.date.today() - datetime.timedelta(days=30))
with col_date2:
    end_date = st.sidebar.date_input("结束日期", value=datetime.date.today())

search_query = st.sidebar.text_input("留空则显示该时间段所有记录", key="search_input_advanced")

if st.sidebar.button('🔎 搜索'):
    # 加载全部聊天历史
    all_chats = load_chat_history()  # 已有函数，返回 [{'role','content'}, ...]

    if not all_chats:
        st.sidebar.info("📭 还没有聊天记录")

    else:
        # 构建对话轮次（user + assistant）
        turns = []
        i = 0
        while i < len(all_chats):
            if all_chats[i]['role'] == 'user':
                user_msg = all_chats[i]
                # 获取时间（优先用消息自带的，否则跳过）
                ts_str = user_msg.get('timestamp')
                if not ts_str:
                    i += 1
                    continue  # 跳过无时间戳的旧消息

                try:
                    msg_time = datetime.datetime.fromisoformat(ts_str)
                    msg_date = msg_time.date()
                except:
                    i += 1
                    continue

                # 检查是否在日期范围内
                if not (start_date <= msg_date <= end_date):
                    i += 1
                    continue

                # 获取 AI 回复
                assistant_msg = ""
                if i + 1 < len(all_chats) and all_chats[i + 1]['role'] == 'assistant':
                    assistant_msg = all_chats[i + 1]['content']

                # 关键词匹配（如果提供了关键词）
                user_text = user_msg['content']
                if search_query.strip():
                    if search_query.lower() not in user_text.lower():
                        i += 1
                        continue

                turns.append({
                    'time': msg_time,
                    'user': user_text,
                    'assistant': assistant_msg
                })
                i += 1
            else:
                i += 1

        if turns:
            # 按时间倒序（最新在前）
            turns.sort(key=lambda x: x['time'], reverse=True)
            st.sidebar.success(f"找到 {len(turns)} 条记录（{start_date} 至 {end_date}）")

            for turn in turns:
                time_str = turn['time'].strftime("%m-%d %H:%M")
                with st.sidebar.expander(f"💬 {time_str}", expanded=False):
                    st.markdown(f"**你**：{turn['user']}")
                    if turn['assistant']:
                        st.markdown(f"**希亚**：{turn['assistant']}")
        else:
            st.sidebar.warning("未找到符合条件的记录")


# 在侧边栏底部
if os.getenv("STREAMLIT_RUNTIME_ENV") == "cloud":
    st.sidebar.caption("☁️ 记忆为临时存储（刷新后清空）")
else:
    st.sidebar.caption("💾 记忆已保存到 memory_list.json")
