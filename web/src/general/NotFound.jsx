import { RoutePaths } from './RoutePaths.jsx';
import { useNavigate } from 'react-router-dom';

export const NotFound = () => {
  const navigate = useNavigate();
  return (
    <div className="flex flex-col items-center">
      <h3 className="px-3 py-2 text-base font-medium">
        Page not found.
      </h3>
      <button onClick={() => navigate(RoutePaths.HOME)} type="button">
        Go Home
      </button>
    </div>
  );
};
