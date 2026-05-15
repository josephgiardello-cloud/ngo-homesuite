"""
Flask error handlers for NGO HomeSuite.

Handles common HTTP errors and custom application errors.
"""

from flask import render_template, request, jsonify
from werkzeug.exceptions import HTTPException


def init_error_handlers(app):
    """Register error handlers with Flask app."""
    
    @app.errorhandler(404)
    def page_not_found(error):
        """Handle 404 errors."""
        if request.accept_mimetypes.get('application/json'):
            return jsonify({
                'error': 'Not Found',
                'status': 404,
                'message': 'The requested resource was not found.'
            }), 404
        return render_template('errors/404.html'), 404
    
    @app.errorhandler(403)
    def forbidden(error):
        """Handle 403 errors."""
        if request.accept_mimetypes.get('application/json'):
            return jsonify({
                'error': 'Forbidden',
                'status': 403,
                'message': 'You do not have permission to access this resource.'
            }), 403
        return render_template('errors/403.html'), 403
    
    @app.errorhandler(500)
    def internal_error(error):
        """Handle 500 errors."""
        if request.accept_mimetypes.get('application/json'):
            return jsonify({
                'error': 'Internal Server Error',
                'status': 500,
                'message': 'An unexpected error occurred. Please try again later.'
            }), 500
        return render_template('errors/500.html'), 500
    
    @app.errorhandler(400)
    def bad_request(error):
        """Handle 400 errors."""
        if request.accept_mimetypes.get('application/json'):
            return jsonify({
                'error': 'Bad Request',
                'status': 400,
                'message': 'The request was invalid.'
            }), 400
        return render_template('errors/400.html'), 400
    
    @app.errorhandler(Exception)
    def handle_exception(error):
        """Handle unhandled exceptions."""
        # Pass through HTTP errors
        if isinstance(error, HTTPException):
            return error
        
        # Log the error
        app.logger.error(f'Unhandled exception: {error}', exc_info=True)
        
        # Return a generic error response
        if request.accept_mimetypes.get('application/json'):
            return jsonify({
                'error': 'Internal Server Error',
                'status': 500,
                'message': 'An unexpected error occurred.'
            }), 500
        return render_template('errors/500.html'), 500
