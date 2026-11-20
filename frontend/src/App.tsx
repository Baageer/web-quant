import { Navigate, Route, Routes } from "react-router-dom";

import MainLayout from "./layouts/MainLayout";
import BacktestPage from "./pages/backtest/BacktestPage";
import KlinePage from "./pages/kline/KlinePage";
import MarketPage from "./pages/market/MarketPage";
import RadarPage from "./pages/radar/RadarPage";

export default function App() {
  return (
    <Routes>
      <Route element={<MainLayout />}>
        <Route index element={<Navigate to="/market" replace />} />
        <Route path="/market" element={<MarketPage />} />
        <Route path="/radar" element={<RadarPage />} />
        <Route path="/kline" element={<KlinePage />} />
        <Route path="/backtest" element={<BacktestPage />} />
        <Route path="*" element={<Navigate to="/market" replace />} />
      </Route>
    </Routes>
  );
}
