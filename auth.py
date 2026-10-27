"""Login do GEOCAPUTI.

Dois modos, escolhidos em st.secrets["auth"]["modo"]:
  - "supabase": confere e-mail/senha no Supabase Auth (usuarios gerenciados no painel do Supabase)
  - "secrets" : lista antiga em st.secrets["users"] (padrao; serve de volta rapida se algo falhar)

Para o modo supabase, os secrets precisam de:
  [auth]
  modo = "supabase"
  [supabase]
  url = "https://<projeto>.supabase.co"
  chave_publica = "sb_publishable_..."   # chave publica, nao e segredo
"""
import hmac

import requests
import streamlit as st

_TIMEOUT = 15


def modo():
    try:
        m = str(st.secrets.get("auth", {}).get("modo", "secrets")).strip().lower()
    except Exception:
        return "secrets"
    if m == "supabase" and "supabase" in st.secrets:
        return "supabase"
    return "secrets"


def _cfg():
    s = st.secrets["supabase"]
    return s["url"].rstrip("/"), s["chave_publica"]


def _guardar_sessao(dados):
    st.session_state["sb_access_token"] = dados.get("access_token")
    st.session_state["sb_refresh_token"] = dados.get("refresh_token")


def entrar(email, senha):
    """Retorna (ok, mensagem_de_erro)."""
    if modo() == "supabase":
        url, chave = _cfg()
        try:
            r = requests.post(f"{url}/auth/v1/token", params={"grant_type": "password"},
                              headers={"apikey": chave}, json={"email": email, "password": senha},
                              timeout=_TIMEOUT)
        except requests.RequestException:
            return False, "⚠️ Não foi possível conectar ao serviço de login. Tente de novo em instantes."
        if r.status_code != 200:
            # mesma mensagem para e-mail inexistente e senha errada (nao revela quem tem conta)
            return False, "❌ E-mail ou senha incorretos."
        _guardar_sessao(r.json())
        return True, None

    if "users" not in st.secrets:
        return False, "⚠️ Configuração de usuários não encontrada nos Secrets."
    # Compara sempre (mesmo com e-mail inexistente), para nao vazar quais e-mails existem.
    senha_esperada = st.secrets["users"].get(email, "")
    if bool(senha_esperada) and hmac.compare_digest(senha, senha_esperada):
        return True, None
    return False, "❌ E-mail ou senha incorretos."


def _renovar_token():
    url, chave = _cfg()
    rt = st.session_state.get("sb_refresh_token")
    if not rt:
        return False
    try:
        r = requests.post(f"{url}/auth/v1/token", params={"grant_type": "refresh_token"},
                          headers={"apikey": chave}, json={"refresh_token": rt}, timeout=_TIMEOUT)
    except requests.RequestException:
        return False
    if r.status_code != 200:
        return False
    _guardar_sessao(r.json())
    return True


def trocar_senha(nova):
    """So no modo supabase. Retorna (ok, mensagem)."""
    url, chave = _cfg()

    def _put():
        return requests.put(f"{url}/auth/v1/user", json={"password": nova}, timeout=_TIMEOUT,
                            headers={"apikey": chave,
                                     "Authorization": f"Bearer {st.session_state.get('sb_access_token')}"})
    try:
        r = _put()
        if r.status_code == 401 and _renovar_token():  # token de 1h venceu: renova e tenta de novo
            r = _put()
    except requests.RequestException:
        return False, "⚠️ Não foi possível conectar ao serviço de login."
    if r.status_code == 200:
        return True, "✅ Senha alterada. Use a nova senha no próximo acesso."
    if r.status_code == 401:
        return False, "⚠️ Sua sessão expirou. Saia e entre de novo para trocar a senha."
    try:
        msg = r.json().get("msg") or r.json().get("message") or ""
    except ValueError:
        msg = ""
    if "same" in msg.lower() or "different" in msg.lower():
        return False, "⚠️ A nova senha precisa ser diferente da atual."
    if "weak" in msg.lower() or "least" in msg.lower():
        return False, "⚠️ Senha fraca demais. Use uma senha mais longa."
    return False, f"⚠️ Não foi possível trocar a senha. {msg}".strip()


def sair():
    if modo() == "supabase" and st.session_state.get("sb_access_token"):
        url, chave = _cfg()
        try:
            requests.post(f"{url}/auth/v1/logout", timeout=_TIMEOUT, headers={
                "apikey": chave, "Authorization": f"Bearer {st.session_state['sb_access_token']}"})
        except requests.RequestException:
            pass  # sair localmente mesmo se o servidor nao responder
    for k in ("logged_in", "user_email", "sb_access_token", "sb_refresh_token"):
        st.session_state.pop(k, None)
