const path = require('path');

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Turbopack config for Next.js 16
  turbopack: {
    resolveAlias: {
      // Optimize JBrowse client-side libraries
      '@jbrowse/react-app': '@jbrowse/react-app',
      '@jbrowse/core': '@jbrowse/core',
    },
    // Disable HMR for problematic MUI CSS modules
    moduleIdStrategy: 'deterministic',
  },
  experimental: {
    serverActions: {},
    // Optimize large module compilation  
    optimizePackageImports: ['@jbrowse/react-app', '@jbrowse/core'],
    // Disable optimizePackageImports for MUI to prevent CSS HMR issues
    // optimizePackageImports: ['@jbrowse/react-app', '@jbrowse/core', '@mui/x-data-grid'],
    
    // Enable Turbopack memory optimizations for large files
    turbo: {
      memoryLimit: 8192, // 8GB for large config compilation
    },
  },
  images: {
    remotePatterns: [
      {
        protocol: 'http',
        hostname: 'localhost',
        port: '54321',
        pathname: '/storage/v1/render/image/**',
      },
      {
        protocol: 'https',
        hostname: 'api.bloom.salk.edu',
        port: '',
        pathname: '/proxy/storage/v1/render/image/**',
      },
      {
        protocol: 'https',
        hostname: 'api.bloom-staging.salkhpi.org',
        port: '',
        pathname: '/storage/v1/render/image/**',
      },
    ],
  },
  webpack: (config, { isServer, dev }) => {
    // Exclude JBrowse and MUI from server-side rendering
    if (isServer) {
      config.externals = [
        ...(config.externals || []),
        '@jbrowse/react-app',
        '@jbrowse/core',
        '@mui/x-data-grid',
      ];
    }
    
    // In development, exclude CSS from node_modules from HMR to prevent module factory errors
    if (dev && !isServer) {
      config.watchOptions = {
        ...config.watchOptions,
        ignored: [
          '**/node_modules/@mui/x-data-grid/**/*.css',
          ...(Array.isArray(config.watchOptions?.ignored) 
            ? config.watchOptions.ignored 
            : [config.watchOptions?.ignored].filter(Boolean)
          ),
        ],
      };
    }
    
    return config;
  },
};

module.exports = nextConfig;
