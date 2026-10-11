import { TaskListPage } from "@/components/TaskListPage";

export default function FromEmailPage() {
  return (
    <TaskListPage
      origin="email"
      title="From Email"
      empty="Actionable emails from your sources show up here on their own."
    />
  );
}
