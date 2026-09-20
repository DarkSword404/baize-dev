import { Link } from 'react-router-dom';
import type { JSX } from 'react';

/**
 * 404 兜底页：未匹配路由统一在此处展示，
 * 而不是静默重定向回首页（避免用户对地址变更感到困惑）。
 */
export function NotFound(): JSX.Element {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 p-6 text-center">
      <div className="text-6xl font-bold text-gray-500 select-none" aria-hidden="true">404</div>
      <h1 className="text-xl font-semibold text-gray-100">页面不存在</h1>
      <p className="max-w-md text-sm text-gray-400">
        你访问的路径未匹配任何功能页。可能是链接已失效，或地址输入有误。
      </p>
      <Link
        to="/dashboard"
        className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500"
      >
        返回控制台
      </Link>
    </div>
  );
}
