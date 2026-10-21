import axios from "axios";
import { toast } from "sonner";

export const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
export const http = axios.create({ baseURL: API });

export function errInfo(e) {
  const env = e?.response?.data;
  if (env?.error) return { code: env.error.code, message: env.error.message, requestId: env.requestId };
  if (env?.detail) return { code: "VALIDATION_ERROR", message: JSON.stringify(env.detail).slice(0, 160) };
  return { code: "NETWORK_ERROR", message: e?.message || "Request failed" };
}

export async function call(method, url, body, okMsg) {
  try {
    const { data } = await http.request({ method, url, data: body });
    if (okMsg) toast.success(okMsg);
    return data;
  } catch (e) {
    const i = errInfo(e);
    toast.error(i.code, { description: `${i.message}${i.requestId ? ` · ${i.requestId}` : ""}` });
    return null;
  }
}

export const pct = (v, d = 2) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(d)}%`);
export const short = (h, n = 10) => (h ? `${h.slice(0, n)}…` : "—");
