import { Compass } from '@phosphor-icons/react'
import { Link } from 'react-router-dom'

import { EmptyState } from '../components/states'
import { Button } from '../components/ui/button'

export default function NotFoundPage() {
  return (
    <EmptyState
      icon={Compass}
      title="No such screen"
      description="That route does not exist, or your role cannot reach it."
      action={
        <Button asChild variant="secondary" size="sm">
          <Link to="/">Back to overview</Link>
        </Button>
      }
    />
  )
}
