import { TaskListPage } from "@/components/TaskListPage";

export default function MyTasksPage() {
  return (
    <TaskListPage
      origin="manual"
      title="My Tasks"
      empty="Add a task of your own with New task. Tasks from your email are under From Email."
    />
  );
}
