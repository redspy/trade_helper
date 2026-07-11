// pm2 프로세스 정의 — Streamlit 대시보드 상시 서비스
// Windows/Linux 겸용: venv 내 python 경로를 플랫폼별로 선택
const path = require("path");
const isWin = process.platform === "win32";
const python = path.join(
  __dirname, ".venv", isWin ? "Scripts" : "bin", isWin ? "python.exe" : "python");

module.exports = {
  apps: [
    {
      name: "trade-dash-web",
      script: python,
      args: "-m streamlit run dashboard/app.py --server.headless true --server.port 8501",
      interpreter: "none",   // python 바이너리를 직접 실행 (node로 감싸지 않음)
      cwd: __dirname,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
    },
  ],
};
