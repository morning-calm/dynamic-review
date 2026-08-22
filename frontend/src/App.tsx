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
import StructureEditorPage from './pages/StructureEditorPage';
import TripDescListPage from './pages/TripDescListPage';
import TripDescPage from './pages/TripDescPage';
import FinalCheckListPage from './pages/FinalCheckListPage';
import FinalCheckPage from './pages/FinalCheckPage';
import PublisherPage from './pages/PublisherPage';
import ReleaseWizardPage from './pages/ReleaseWizardPage';

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
            <Route path="/descriptions" element={<TripDescListPage />} />
            <Route path="/descriptions/:tgId" element={<TripDescPage />} />
            <Route element={<RequireAdmin />}>
              <Route path="/queue" element={<ReviewQueuePage />} />
              <Route path="/staging" element={<StagingSearchPage />} />
              <Route path="/structure/:tripId" element={<StructureEditorPage />} />
              <Route path="/final-check" element={<FinalCheckListPage />} />
              <Route path="/final-check/:tripId" element={<FinalCheckPage />} />
              <Route path="/publisher" element={<PublisherPage />} />
              <Route path="/publisher/release/:tripId" element={<ReleaseWizardPage />} />
              <Route path="/publisher/release-family/:tgId" element={<ReleaseWizardPage />} />
              <Route path="/publisher/release-location/:locName" element={<ReleaseWizardPage />} />
              <Route path="/publisher/release-batch/:batchId" element={<ReleaseWizardPage />} />
            </Route>
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </div>
  </BrowserRouter>
);

export default App;
