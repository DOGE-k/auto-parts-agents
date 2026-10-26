import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import BrowserLoginGate from "./components/BrowserLoginGate";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserLoginGate><App /></BrowserLoginGate>
  </React.StrictMode>,
);
