/* DisplaySwitcher 前端桥接层 —— 通过本地 REST API 与真实显示控制通信 */
const API = {
  async state()      { return (await fetch('/api/state')).json(); },
  async system()     { return (await fetch('/api/system')).json(); },
  async apply(w, h, f, confirm) {
    return (await fetch('/api/apply', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ width: w, height: h, frequency: f, confirm }) })).json();
  },
  async addProfile(p){
    return (await fetch('/api/profile', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(p) })).json();
  },
  async applyProfile(id){
    return (await fetch('/api/profile/apply', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id }) })).json();
  },
  async deleteProfile(id){ return (await fetch('/api/profile/' + id, { method: 'DELETE' })).json(); },
  async getSettings(){ return (await fetch('/api/settings')).json(); },
  async setSettings(s){
    return (await fetch('/api/settings', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(s) })).json();
  },
  async setAutostart(en){
    return (await fetch('/api/autostart', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: en }) })).json();
  },
  async confirm(){ return (await fetch('/api/confirm', { method: 'POST' })).json(); },
  async revert(){  return (await fetch('/api/revert',  { method: 'POST' })).json(); },
  async checkUpdate(){ return (await fetch('/api/update/check')).json(); },
};

/* 解析 "2560×1440" / "2560 x 1440" -> {w,h} */
function parseRes(str){
  const m = String(str).match(/(\d+)\D+(\d+)/);
  return m ? { w: +m[1], h: +m[2] } : { w: 0, h: 0 };
}
