"""코트 호출 안내 방송: "1코트, 누구, 누구 님 들어가세요"를 Supertonic 3(한국어, CPU ONNX)으로 읽는다.
모델(~400MB)은 Docker 이미지를 만들 때 받아 두고, 처음 호출될 때 한 번만 메모리에 올린다."""
import io
import re
import threading
from functools import lru_cache

VOICE = "F1"  # Supertonic 기본 목소리 M1~M5, F1~F5


def announcement_text(court: int, names: list[str]) -> str:
    spoken = [re.sub(r"[^0-9A-Za-z가-힣 ]", "", n).strip() or "선수" for n in names]  # 이모지·기호는 읽지 않는다
    return f"{court}코트, {', '.join(spoken)} 님 들어가세요."


_load_lock = threading.Lock()


@lru_cache(maxsize=1)
def _load():
    from supertonic import TTS
    tts = TTS(auto_download=False)
    return tts, tts.get_voice_style(voice_name=VOICE)


def _engine():
    with _load_lock:  # 두 화면이 동시에 처음 불러도 모델은 한 번만 올린다
        return _load()


@lru_cache(maxsize=128)  # 같은 경기를 여러 화면이 불러도 한 번만 합성
def speak(text: str) -> bytes:
    import soundfile
    tts, style = _engine()
    wav, _ = tts.synthesize(text, voice_style=style, lang="ko")
    buf = io.BytesIO()
    soundfile.write(buf, wav.squeeze(), tts.sample_rate, format="MP3")  # 5초에 약 45KB (WAV는 470KB)
    return buf.getvalue()
