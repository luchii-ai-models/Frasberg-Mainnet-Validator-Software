import "@/App.css";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import Shell from "@/components/Shell";
import Overview from "@/pages/Overview";
import Versions from "@/pages/Versions";
import Policies from "@/pages/Policies";
import Keys from "@/pages/Keys";
import Locks from "@/pages/Locks";
import Audit from "@/pages/Audit";
import Outbox from "@/pages/Outbox";
import Metrics from "@/pages/Metrics";
import Rollbacks from "@/pages/Rollbacks";
import Tests from "@/pages/Tests";
import Readiness from "@/pages/Readiness";
import Fleet from "@/pages/Fleet";
import Alerts from "@/pages/Alerts";
import Upgrades from "@/pages/Upgrades";

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<Overview />} />
          <Route path="versions" element={<Versions />} />
          <Route path="policies" element={<Policies />} />
          <Route path="keys" element={<Keys />} />
          <Route path="locks" element={<Locks />} />
          <Route path="audit" element={<Audit />} />
          <Route path="outbox" element={<Outbox />} />
          <Route path="metrics" element={<Metrics />} />
          <Route path="rollbacks" element={<Rollbacks />} />
          <Route path="tests" element={<Tests />} />
          <Route path="fleet" element={<Fleet />} />
          <Route path="upgrades" element={<Upgrades />} />
          <Route path="alerts" element={<Alerts />} />
          <Route path="readiness" element={<Readiness />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
