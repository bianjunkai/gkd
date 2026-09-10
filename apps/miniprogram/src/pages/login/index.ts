import { type Health } from '@gkd/client';
import { api, operate, setSession, state } from '../../runtime';
import { API_BASE_URL } from '../../config';

Page({
  data: { busy: false, error: '', health: null as Health | null, register: false, username: '', password: '', display_name: '', apiBase: API_BASE_URL },
  onLoad() { void operate(this, async () => { this.setData({ health: await api.health() }); }); },
  onShow() { if (state().session) wx.switchTab({ url: '/pages/inbox/index' }); },
  input(event: WxInputEvent) { this.setData({ [String(event.currentTarget.dataset.field)]: event.detail.value }); },
  toggleRegister() { this.setData({ register: !this.data.register, error: '' }); },
  submit() { void operate(this, async () => {
    const result = this.data.register ? await api.register(this.data.username, this.data.password, this.data.display_name) : await api.login(this.data.username, this.data.password);
    setSession(result); this.setData({ password: '' }); wx.reLaunch({ url: '/pages/inbox/index' });
  }); },
  wechatLogin() { void operate(this, async () => {
    const code = await new Promise<string>((resolve, reject) => wx.login({ success: result => result.code ? resolve(result.code) : reject(new Error('未取得微信登录凭据，请重试。')), fail: () => reject(new Error('微信登录失败，请重试。')) }));
    setSession(await api.wechatLogin(code)); wx.reLaunch({ url: '/pages/inbox/index' });
  }); },
  retry() { this.onLoad(); },
});
