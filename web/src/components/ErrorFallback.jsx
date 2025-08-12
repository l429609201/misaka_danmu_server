import { useNavigate } from "react-router-dom";
import { RoutePaths } from "../general/RoutePaths.jsx";

export const ErrorFallback = ({ error, resetErrorBoundary }) => {
  const navigate = useNavigate();

  return (
    <div role="alert">
      {import.meta.env.DEV && (
        <div className="bg-white pl-10 pt-10">
          <p>DEV ONLY!</p>
          <p>Something went wrong:</p>
          <pre>{error.message}</pre>
          <button onClick={resetErrorBoundary}>Try again</button>
        </div>
      )}
      <div className="mb-4 mt-16 flex w-full flex-col items-center justify-center space-y-16">
        <div>
          <a
            onClick={() => {
              resetErrorBoundary();
              navigate(RoutePaths.HOME);
            }}
          >
            Home
          </a>
        </div>
        <div>An error happened. Contact support please!</div>
      </div>
    </div>
  );
};
