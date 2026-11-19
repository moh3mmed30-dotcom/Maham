window.TelegramApp = {
  initData: window.Telegram?.WebApp?.initData || "",
  user: window.Telegram?.WebApp?.initDataUnsafe?.user || null,
  init(){ if(window.Telegram?.WebApp){Telegram.WebApp.ready();Telegram.WebApp.expand();} },
  headers(){return {"Content-Type":"application/json","X-Telegram-Init-Data":this.initData};}
};
