import { Link } from 'react-router';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/common/Feedback';
import { PageHeader } from '@/components/common/PageHeader';

export function NotFoundPage() {
  return (
    <div className="flex h-full flex-col">
      <PageHeader title="迷路的星图" />
      <EmptyState
        className="flex-1"
        title="这一页不在星图档案里"
        description="也许它被收进了另一本档案册。"
        action={
          <Button asChild variant="outline">
            <Link to="/">回到 Nova 身边</Link>
          </Button>
        }
      />
    </div>
  );
}
