import React from 'react';
import { Outlet } from 'react-router-dom';
import { PageFrame } from './Footer';
import { Header } from './Header';
import { useDeployments } from './DeploymentsProvider';
import { useDocumentTitle } from './title';

/** Every signed-in page: the header, then the page, then the footer. */
export const AppShell: React.FC = () => {
  useDocumentTitle(useDeployments().find);
  return (
    <PageFrame header={<Header />}>
      <Outlet />
    </PageFrame>
  );
};

/** The sign-in pages: the page and the footer. */
export const BareShell: React.FC = () => {
  useDocumentTitle();
  return (
    <PageFrame>
      <Outlet />
    </PageFrame>
  );
};
