import { PersistQueryClientProvider } from '@tanstack/react-query-persist-client'
import { RouterProvider } from 'react-router'
import { router } from './app/router'
import { persistOptions, queryClient } from './lib/api/queryClient'

function App() {
  return (
    <PersistQueryClientProvider client={queryClient} persistOptions={persistOptions}>
      <RouterProvider router={router} />
    </PersistQueryClientProvider>
  )
}

export default App
