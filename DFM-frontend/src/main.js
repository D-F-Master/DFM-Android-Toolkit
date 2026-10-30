import{createApp}from"vue";import ElementPlus from"element-plus";import"element-plus/dist/index.css";import"element-plus/theme-chalk/dark/css-vars.css";import"./main.css";import App from"./App.vue";
// Element Plus 暗色模式：跟随系统主题（项目内其他组件用 prefers-color-scheme 判断，这里同步到 html.dark）
const darkQuery=window.matchMedia?window.matchMedia("(prefers-color-scheme: dark)"):null;
const syncThemeClass=()=>{document.documentElement.classList.toggle("dark",!!darkQuery?.matches)};
syncThemeClass();
darkQuery?.addEventListener?.("change",syncThemeClass);
const app=createApp(App);app.use(ElementPlus);app.mount("#app");
