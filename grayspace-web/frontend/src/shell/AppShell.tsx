import React from 'react';
import { Outlet } from 'react-router-dom';
import { PageFrame } from './Footer';
import { Header } from './Header';

/** Every signed-in page: the header, then the page, then the footer. */
export const AppShell: React.FC = () => (
  <PageFrame header={<Header />}>
    <Outlet />
  </PageFrame>
);

/** The sign-in pages: the page and the footer. */
export const BareShell: React.FC = () => (
  <PageFrame>
    <Outlet />
  </PageFrame>
);
