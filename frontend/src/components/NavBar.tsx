import { Link } from 'react-router-dom';
import type { ReactNode } from 'react';
import UserMenu from './UserMenu';

interface NavBarProps {
  title: string;
  subtitle?: string;
  /** Right-aligned slot (save status, action buttons). */
  right?: ReactNode;
  /** Back link target; defaults to the trip list. `null` on the trip list itself, which
   * has nowhere to go back to. */
  backTo?: string | null;
  backLabel?: string;
}

const NavBar = ({ title, subtitle, right, backTo = '/', backLabel = 'Trips' }: NavBarProps) => (
  <header className="sticky top-0 z-20 border-b border-gray-800 bg-gray-900/95 backdrop-blur">
    <div className="mx-auto flex max-w-review flex-wrap items-center justify-between gap-4 gap-y-2 px-4 py-3">
      <div className="min-w-0">
        {backTo && (
          <Link to={backTo} className="text-xs text-custom-green hover:underline">
            ← {backLabel}
          </Link>
        )}
        <h1 className="truncate text-lg font-semibold text-white">{title}</h1>
        {subtitle && <p className="truncate text-xs text-gray-400">{subtitle}</p>}
      </div>
      <div className="flex w-full flex-wrap items-center justify-end gap-3 sm:w-auto">
        {right}
        <UserMenu />
      </div>
    </div>
  </header>
);

export default NavBar;
