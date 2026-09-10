import { type AuthResult } from '@gkd/client';
import { SESSION_KEY, type GuikeApp } from './runtime';

App({
  globalData: { session: null, profile: null, lastChange: null } as GuikeApp['globalData'],
  onLaunch() {
    try { const session = wx.getStorageSync<AuthResult>(SESSION_KEY); if (session?.token && session.user?.id && session.workspace?.id) this.globalData.session = session; }
    catch { this.globalData.session = null; }
  },
});
