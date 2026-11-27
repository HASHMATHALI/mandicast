import { useEffect } from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router";
import { AlertsPage } from "./pages/AlertsPage";
import { ComparePage } from "./pages/ComparePage";
import { DataPage } from "./pages/DataPage";
import { HomePage } from "./pages/HomePage";
import { LoginPage } from "./pages/LoginPage";
import { MandiPage } from "./pages/MandiPage";
import { t } from "./strings";

function NotFound() {
  return (
    <section>
      <h1>{t.notFound}</h1>
      <p><Link to="/">{t.backHome}</Link></p>
    </section>
  );
}

export function App() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);

  return (
    <>
      <a className="skip-link" href="#main">{t.skipToContent}</a>
      <header className="app-header">
        <div className="inner">
          <Link to="/" className="brand"><img src="/favicon.svg" alt="" />{t.appName}</Link>
          <nav className="nav" aria-label="Main">
            <NavLink to="/" end>{t.nav.prices}</NavLink>
            <NavLink to="/compare">{t.nav.compare}</NavLink>
            <NavLink to="/data">{t.nav.data}</NavLink>
            <NavLink to="/alerts">{t.nav.alerts}</NavLink>
          </nav>
        </div>
      </header>
      <main id="main" tabIndex={-1}>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/mandi/:marketId" element={<MandiPage />} />
          <Route path="/compare" element={<ComparePage />} />
          <Route path="/data" element={<DataPage />} />
          <Route path="/alerts" element={<AlertsPage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
      <footer className="app-footer">
        <p>
          Prices are as reported by the market committees (APMCs) to AGMARKNET and published on the Open Government
          Data Platform India under the Government Open Data License – India. MandiCast is not endorsed by the data
          provider. Forecasts are estimates from past prices, not guarantees, and not advice to buy or sell.
        </p>
      </footer>
    </>
  );
}
