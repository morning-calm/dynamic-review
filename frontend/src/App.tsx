import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ToastContainer } from 'react-toastify';
import 'react-toastify/dist/ReactToastify.css';
import TripListPage from './pages/TripListPage';
import ReviewPage from './pages/ReviewPage';
import ChangesSummaryPage from './pages/ChangesSummaryPage';

const App = () => (
  <BrowserRouter>
    <div className="dark min-h-screen w-full bg-gray-900 text-gray-100">
      <ToastContainer theme="dark" position="bottom-right" />
      <Routes>
        <Route path="/" element={<TripListPage />} />
        <Route path="/review/:sid" element={<ReviewPage />} />
        <Route path="/admin/:sid" element={<ChangesSummaryPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  </BrowserRouter>
);

export default App;
