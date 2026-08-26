import React from "react";
import { createRoot } from "react-dom/client";
import { ConfigProvider } from "antd";
import App from "./App.jsx";
import "./styles.css";

createRoot(document.getElementById("root")).render(
  <ConfigProvider
    theme={{
      token: {
        colorPrimary: "#0d0d0d",
        colorInfo: "#0d0d0d",
        colorLink: "#0d0d0d",
        borderRadius: 10,
        fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Noto Sans SC', 'Microsoft YaHei', sans-serif",
        fontSize: 13.5,
        colorBgLayout: "#fdfdfc",
        colorBgContainer: "#ffffff",
        colorText: "#141413",
        colorTextSecondary: "#8d8d88",
        colorBorder: "#e4e4e0",
        colorBorderSecondary: "#ececeb",
        boxShadow: "none",
        boxShadowSecondary: "0 12px 32px rgba(20,20,18,.07)",
        controlHeight: 34,
        wireframe: false,
      },
      components: {
        Button: {
          primaryColor: "#ffffff",
          fontWeight: 550,
          defaultBorderColor: "#e4e4e0",
          defaultBg: "#ffffff",
        },
        Menu: {
          itemBg: "transparent",
          itemSelectedBg: "#efefec",
          itemSelectedColor: "#141413",
          itemColor: "#6f6f69",
          itemHeight: 38,
          itemMarginInline: 10,
          itemBorderRadius: 10,
          activeBarBorderWidth: 0,
        },
        Segmented: {
          trackBg: "#f2f2ef",
          itemSelectedBg: "#ffffff",
          borderRadiusSM: 99,
        },
        Tag: { borderRadiusSm: 99, defaultBg: "#ffffff" },
        Card: { borderRadiusLG: 14 },
        Splitter: { splitBarSize: 1, splitTriggerSize: 10 },
      },
    }}
  >
    <App />
  </ConfigProvider>,
);
