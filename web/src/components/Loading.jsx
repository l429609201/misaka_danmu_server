export const Loading = ({ name }) => {
  if (name) {
    console.log('Loading', name);
  }

  return (
    <div className="flex h-full flex-col items-center justify-center">
      <div className="h-32 w-32 rounded-full border-b-2 border-gray-900">
        loading
      </div>
    </div>
  );
};
