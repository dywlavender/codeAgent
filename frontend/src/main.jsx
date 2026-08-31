import React from "react";
import { createRoot } from "react-dom/client";
import { ConfigProvider } from "antd";
import App from "./App.jsx";
import { palette } from "./theme.js";
import "./styles.css";

createRoot(document.getElementById("root")).render(
  <ConfigProvider
    theme={{
      token: {
        colorPrimary: palette.brand,
        colorInfo: palette.brand,
        colorLink: palette.brand,
        borderRadius: 10,
        fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Noto Sans SC', 'Microsoft YaHei', sans-serif",
        fontSize: 13.5,
        colorBgLayout: palette.canvas,
        colorBgContainer: palette.raised,
        colorText: palette.text,
        colorTextSecondary: palette.textSecondary,
        colorBorder: palette.border,
        colorBorderSecondary: palette.border,
        boxShadow: "none",
        boxShadowSecondary: palette.shadow2,
        controlHeight: 34,
        wireframe: false,
      },
      components: {
        Button: {
          primaryColor: "#ffffff",
          fontWeight: 550,
          defaultBorderColor: palette.border,
          defaultBg: palette.raised,
          primaryShadow: "none",
          defaultShadow: "none",
        },
        Menu: {
          itemBg: "transparent",
          itemSelectedBg: "rgba(27,107,101,.10)",
          itemSelectedColor: palette.brandStrong,
          itemColor: palette.textSecondary,
          itemHeight: 38,
          itemMarginInline: 10,
          itemBorderRadius: 10,
          activeBarBorderWidth: 0,
        },
        Segmented: {
          trackBg: palette.muted,
          itemSelectedBg: palette.raised,
          borderRadiusSM: 99,
        },
        Tag: { borderRadiusSm: 99, defaultBg: palette.raised },
        Card: { borderRadiusLG: 14 },
        Splitter: { splitBarSize: 1, splitTriggerSize: 10 },
        Alert: { borderRadius: 12 },
      },
    }}
  >
    <App />
  </ConfigProvider>,
);
