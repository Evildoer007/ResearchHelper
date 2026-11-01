import { installThemeControls } from "/login/theme.js";
import { initializeVolSurface } from "/login/vol-surface.js";

const form = document.querySelector("#login-form");
const account = document.querySelector("#login-account");
const password = document.querySelector("#login-password");
const remember = document.querySelector("#login-remember");
const submit = document.querySelector("#login-submit");
const status = document.querySelector("#login-status");
const eye = document.querySelector("#login-eye");
let token = "";
let ready = false;
let busy = false;
function setBusy(value, freezeCredentials = false) {
  busy = value;
  form.setAttribute("aria-busy", String(value));
  submit.disabled = value;
  for (const control of [account, password, remember, eye]) control.disabled = value && freezeCredentials;
}
function workspaceURL() { const query = new URLSearchParams(location.search); query.delete("account"); return "/" + (query.size ? "?" + query : ""); }
const surface = initializeVolSurface(document.querySelector("#login-vol-surface"));
window.__volIn = () => surface?.reveal();
if (window.__researchhelperVolInRequested) window.__volIn();
installThemeControls(document.querySelector(".login-theme"));

async function request(path, data) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(path, {
      method: data ? "POST" : "GET", credentials: "same-origin", cache: "no-store",
      signal: controller.signal,
      headers: data ? { "Content-Type": "application/json", "X-ResearchHelper-Token": token } : {},
      ...(data ? { body: JSON.stringify(data) } : {}),
    });
    let body;
    try { body = await response.json(); }
    catch { throw Object.assign(new Error("本地服务响应异常，请重新连接。"), { reconnect: true }); }
    if (!response.ok) throw Object.assign(new Error(body.error || body.message || `请求失败（${response.status}），请重试。`), {
      status: response.status, reconnect: response.status >= 500 || body.failure_code === "local_token_invalid",
    });
    return body;
  } catch (error) {
    if (error.name === "AbortError") throw Object.assign(new Error("连接超时，请重新连接。"), { reconnect: true });
    if (error instanceof TypeError) throw Object.assign(new Error("无法连接本地服务，请重新连接。"), { reconnect: true });
    throw error;
  } finally { clearTimeout(timeout); }
}

async function bootstrap() {
  if (busy) return;
  setBusy(true);
  ready = false;
  token = "";
  submit.textContent = "正在连接";
  status.textContent = "";
  try {
    const state = await request("/api/auth/status");
    if (state.authenticated) { ready = true; location.assign(workspaceURL()); return; }
    if (!state.token) throw new Error("登录凭据未就绪，请重新启动ResearchHelper后重试。");
    if (!state.configured) throw new Error("本地账户尚未就绪，请重新启动ResearchHelper后重试。");
    token = state.token;
    const requestedAccount = new URLSearchParams(location.search).get("account");
    if (!account.value && ["admin", "sales"].includes(requestedAccount)) account.value = requestedAccount;
    submit.textContent = "登录";
    ready = true;
  } catch (error) {
    status.textContent = error.message;
    submit.textContent = "重新连接";
  } finally {
    setBusy(false);
    submit.textContent = ready ? "登录" : "重新连接";
  }
}

function clearValidation() {
  for (const field of [account, password]) {
    field.removeAttribute("aria-invalid");
    const descriptions = (field.getAttribute("aria-describedby") || "").split(/\s+/).filter(id => id && id !== "login-status");
    if (descriptions.length) field.setAttribute("aria-describedby", descriptions.join(" "));
    else field.removeAttribute("aria-describedby");
  }
}

function rejectField(field, message) {
  field.setAttribute("aria-invalid", "true");
  field.setAttribute("aria-describedby", "login-status");
  status.textContent = message;
  field.focus();
}

form.addEventListener("input", () => {
  if (form.querySelector('[aria-invalid="true"]')) {
    clearValidation();
    status.textContent = "";
  }
});
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  if (!ready) { await bootstrap(); return; }
  clearValidation();
  status.textContent = "";
  if (!account.value.trim()) return rejectField(account, "请填写admin或sales账号。");
  if (!password.value) return rejectField(password, "请填写密码。");
  setBusy(true, true);
  submit.textContent = "正在登录";
  let focusPassword = false;
  try {
    await request("/api/auth/login", { account: account.value.trim(), password: password.value, remember: remember.checked });
    password.value = "";
    location.assign(workspaceURL());
  } catch (error) {
    status.textContent = error.message;
    if (error.reconnect) { ready = false; token = ""; }
    else if (error.status === 401) {
      for (const field of [account, password]) {
        field.setAttribute("aria-invalid", "true");
        field.setAttribute("aria-describedby", "login-status");
      }
      focusPassword = true;
    }
  } finally {
    setBusy(false);
    submit.textContent = ready ? "登录" : "重新连接";
    if (focusPassword) password.focus({ preventScroll: true });
  }
});

eye.addEventListener("click", () => {
  const visible = eye.getAttribute("aria-pressed") === "true";
  eye.setAttribute("aria-pressed", String(!visible));
  eye.setAttribute("aria-label", visible ? "显示密码" : "隐藏密码");
  eye.title = eye.getAttribute("aria-label");
  password.type = visible ? "password" : "text";
  password.focus({ preventScroll: true });
});

// The reference card follows the pointer with a single scheduled update.
const card = document.querySelector(".login-card");
if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
  let queued = false;
  let x = 50;
  let y = 0;
  card.addEventListener("pointermove", (event) => {
    const bounds = card.getBoundingClientRect();
    x = (event.clientX - bounds.left) / bounds.width * 100;
    y = (event.clientY - bounds.top) / bounds.height * 100;
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      card.style.setProperty("--login-pointer-x", `${x.toFixed(1)}%`);
      card.style.setProperty("--login-pointer-y", `${y.toFixed(1)}%`);
      queued = false;
    });
  }, { passive: true });
}
bootstrap();
