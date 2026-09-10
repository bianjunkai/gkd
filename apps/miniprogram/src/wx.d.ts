// Minimal declarations for the public WeChat APIs actually used by this app.
// These are not a substitute for a real Developer Tools / device compatibility check.
interface WxInputEvent { detail: { value: string }; currentTarget: { dataset: Record<string, string | number> } }
interface WxTapEvent { currentTarget: { dataset: Record<string, string | number> } }
interface WxCheckboxEvent { detail: { value: string[] }; currentTarget: { dataset: Record<string, string | number> } }
interface WxSwitchEvent { detail: { value: boolean } }
interface WxFailure { errMsg: string }
interface WxModalResult { confirm: boolean; cancel: boolean; content?: string }
declare const wx: {
  request<T = unknown>(options: { url: string; method?: 'GET' | 'POST' | 'PATCH'; header?: Record<string, string>; data?: unknown; timeout?: number;
    success: (value: { statusCode: number; data: T }) => void; fail: (error: WxFailure) => void }): { abort(): void };
  login(options: { success: (value: { code: string }) => void; fail: (error: WxFailure) => void }): void;
  getStorageSync<T = unknown>(key: string): T | undefined;
  setStorageSync(key: string, data: unknown): void;
  removeStorageSync(key: string): void;
  navigateTo(options: { url: string }): void;
  redirectTo(options: { url: string }): void;
  reLaunch(options: { url: string }): void;
  switchTab(options: { url: string }): void;
  navigateBack(options?: { delta?: number }): void;
  showToast(options: { title: string; icon?: 'none' | 'success'; duration?: number }): void;
  showModal(options: { title: string; content?: string; showCancel?: boolean; confirmText?: string; cancelText?: string;
    editable?: boolean; placeholderText?: string; success: (result: WxModalResult) => void }): void;
  stopPullDownRefresh(): void;
  setClipboardData(options: { data: string }): void;
  downloadFile(options: { url: string; header?: Record<string, string>; timeout?: number;
    success: (value: { statusCode: number; tempFilePath: string }) => void; fail: (error: WxFailure) => void }): { abort(): void };
  shareFileMessage?: (options: { filePath: string; fileName?: string; success?: () => void; fail?: (error: WxFailure) => void }) => void;
};
declare function App<T extends object>(options: T & ThisType<T>): void;
declare function getApp<T = { globalData: Record<string, unknown> }>(): T;
declare function Page<T extends { data: object }>(options: T & ThisType<T & { setData(data: Record<string, unknown>, callback?: () => void): void }>): void;
declare function setTimeout(callback: () => void, duration: number): number;
declare function clearTimeout(id: number): void;
