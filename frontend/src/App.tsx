import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ToastContainer } from 'react-toastify';
import 'react-toastify/dist/ReactToastify.css';
import { AuthProvider } from './AuthProvider';
import { RequireAdmin, RequireAuth } from './components/RouteGuards';
import LoginPage from './pages/LoginPage';
import TripListPage from './pages/TripListPage';
import ReviewPage from './pages/ReviewPage';
import ChangesSummaryPage from './pages/ChangesSummaryPage';
import ReviewQueuePage from './pages/ReviewQueuePage';
import CompletedPage from './pages/CompletedPage';
import BugReportsPage from './pages/BugReportsPage';
import StagingSearchPage from './pages/StagingSearchPage';

const App = () => (
  <BrowserRouter>
    <div className="dark min-h-screen w-full bg-gray-900 text-gray-100">
      <ToastContainer theme="dark" position="bottom-right" />
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<RequireAuth />}>
            <Route path="/" element={<TripListPage />} />
            <Route path="/review/:sid" element={<ReviewPage />} />
            <Route path="/admin/:sid" element={<ChangesSummaryPage />} />
            <Route path="/completed" element={<CompletedPage />} />
            <Route path="/bugs" element={<BugReportsPage />} />
            <Route element={<RequireAdmin />}>
              <Route path="/queue" element={<ReviewQueuePage />} />
              <Route path="/staging" element={<StagingSearchPage />} />
            </Route>
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </div>
  </BrowserRouter>
);

export default App;
